import json, math, os, time, boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb")
sns = boto3.client("sns")
iot = (boto3.client("iot-data", endpoint_url="https://" + os.environ["IOT_ENDPOINT"])
       if os.environ.get("IOT_ENDPOINT") else None)
tele = ddb.Table(os.environ["TELEMETRY_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])

# 1) Fixed threshold rules (automated threshold alerts)
LIMITS = {"AirQuality": 150, "CO2": 1000, "PowerConsumption": 50, "Temperature": 35}
# Critical levels: the system never takes critical actions by itself, it escalates to an operator
CRITICAL = {"AirQuality": 250, "CO2": 2000, "PowerConsumption": 75, "Temperature": 40}

# 2) Machine learning anomaly detection (unsupervised, multivariate Gaussian model)
FEATURES = ["Temperature", "Humidity", "AirQuality", "CO2", "PowerConsumption"]
TRAIN_SIZE = 200         # recent readings used to learn "normal" for each zone
MIN_TRAIN = 50           # ML starts once a zone has this many readings
RETRAIN_SECS = 300       # the model is re-learned every 5 minutes
ANOMALY_LIMIT = 36.0     # anomaly score (Mahalanobis distance squared) above this = anomaly
COOLDOWN_SECS = 900      # max one alert (and one automatic action) per device per 15 minutes

# 3) Automatic corrective actions (safe, low-risk, with safety checks)
COMMAND_TOPIC = "campus/building/commands"
AUTO_ENABLED = os.environ.get("AUTO_ACTIONS", "true").lower() == "true"   # kill switch
ACTION_MINUTES = 15
SETPOINT_MIN, SETPOINT_MAX, ECO_SETPOINT = 22, 26, 26
SANE = {"Temperature": (-10, 60), "Humidity": (0, 100), "AirQuality": (0, 500),
        "CO2": (250, 10000), "PowerConsumption": (0, 500)}   # plausibility check on sensor data

_models = {}             # trained models are kept between Lambda invocations


def invert(m):
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        piv = a[c][c]
        a[c] = [v / piv for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def score(model, x):
    mean, sd, inv = model
    d = len(x)
    z = [(x[j] - mean[j]) / sd[j] for j in range(d)]
    d2 = sum(z[i] * inv[i][j] * z[j] for i in range(d) for j in range(d))
    worst = max(range(d), key=lambda j: abs(z[j]))
    return d2, worst


def fit(rows):
    """Learn mean, spread and covariance of normal behaviour. Outliers are dropped and the model is refit once."""
    use = rows
    for _ in range(2):
        n, d = len(use), len(use[0])
        mean = [sum(r[j] for r in use) / n for j in range(d)]
        sd = []
        for j in range(d):
            s = math.sqrt(sum((r[j] - mean[j]) ** 2 for r in use) / n)
            sd.append(max(s, 0.02 * abs(mean[j]) + 0.1))
        z = [[(r[j] - mean[j]) / sd[j] for j in range(d)] for r in use]
        cov = [[sum(zr[i] * zr[j] for zr in z) / n + (0.02 if i == j else 0.0)
                for j in range(d)] for i in range(d)]
        model = (mean, sd, invert(cov))
        keep = [r for r in rows if score(model, r)[0] <= ANOMALY_LIMIT]
        if len(keep) < 30 or len(keep) == len(use):
            break
        use = keep
    return model


def get_model(dev):
    cached = _models.get(dev)
    if cached and time.time() - cached[0] < RETRAIN_SECS:
        return cached[1]
    items = tele.query(KeyConditionExpression=Key("DeviceID").eq(dev),
                       ScanIndexForward=False, Limit=TRAIN_SIZE)["Items"]
    if len(items) < MIN_TRAIN:
        return None
    model = fit([[float(h[f]) for f in FEATURES] for h in items])
    _models[dev] = (time.time(), model)
    return model


def diagnose(x, model, ml_flag, ml_worst):
    """Turn readings into named problems, each with a recommended corrective action."""
    v = dict(zip(FEATURES, x))
    pi = FEATURES.index("PowerConsumption")
    above_normal = bool(model) and ml_flag and x[ml_worst] > model[0][ml_worst]
    ml_feature = FEATURES[ml_worst] if ml_flag else None
    problems = []
    if (v["CO2"] > LIMITS["CO2"] or v["AirQuality"] > LIMITS["AirQuality"]
            or (above_normal and ml_feature in ("CO2", "AirQuality"))):
        problems.append({"id": "air", "title": "Poor indoor air quality",
            "recommendation": "Improve ventilation: raise fresh-air intake or open vents, reduce occupancy and check air filters.",
            "critical": v["CO2"] > CRITICAL["CO2"] or v["AirQuality"] > CRITICAL["AirQuality"]})
    if (v["PowerConsumption"] > LIMITS["PowerConsumption"]
            or (above_normal and ml_feature == "PowerConsumption")):
        problems.append({"id": "energy", "title": "Excessive energy consumption",
            "recommendation": "Check HVAC operating schedules and look for equipment left running outside occupied hours.",
            "critical": v["PowerConsumption"] > CRITICAL["PowerConsumption"]})
    working_hard = bool(model) and (x[pi] - model[0][pi]) / model[1][pi] > 2
    if v["Temperature"] > LIMITS["Temperature"] or (v["Temperature"] > 28 and working_hard):
        problems.append({"id": "hvac", "title": "Possible HVAC inefficiency",
            "recommendation": "Inspect the HVAC unit (filters, refrigerant level, thermostat setpoint): the zone is hot despite high energy use.",
            "critical": v["Temperature"] > CRITICAL["Temperature"]})
    if ml_flag and not problems:
        problems.append({"id": "unusual", "title": "Unusual sensor behaviour",
            "recommendation": f"Check the equipment and the sensor in this zone: {ml_feature} is far from its learned normal.",
            "critical": False})
    return problems


def send_command(cmd):
    iot.publish(topic=COMMAND_TOPIC, qos=1, payload=json.dumps(cmd))


def act(problems, x, dev):
    """Run predefined low-risk actions, with safety checks. Returns a log of what happened and why."""
    v = dict(zip(FEATURES, x))
    log = []
    for p in problems:
        if p["critical"]:
            log.append(f"CRITICAL level ({p['title']}): no automatic shutdown or load shedding. Operator approval is required.")
    wanted = []
    if any(p["id"] == "air" for p in problems):
        wanted.append(("ventilation_boost", {"Minutes": ACTION_MINUTES}, "air quality"))
    if any(p["id"] == "energy" and not p["critical"] for p in problems):
        if v["Temperature"] <= SETPOINT_MAX + 0.5:
            wanted.append(("hvac_eco_mode", {"Setpoint": ECO_SETPOINT, "Minutes": ACTION_MINUTES}, "energy use"))
        else:
            log.append("HVAC eco mode skipped: the zone is too warm to reduce cooling.")
    if any(p["id"] in ("hvac", "unusual") for p in problems):
        log.append("Needs a technician: no automatic action is defined for this problem.")
    if not wanted:
        return log
    if not AUTO_ENABLED:
        return log + ["Automatic actions are switched off (kill switch)."]
    if iot is None:
        return log + ["Automatic actions unavailable: command channel not configured."]
    bad = [f for f, (lo, hi) in SANE.items() if not lo <= v[f] <= hi]
    if bad:
        return log + [f"Automatic actions blocked: sensor values failed the plausibility check ({', '.join(bad)})."]
    for action, params, why in wanted:
        cmd = {"DeviceID": dev, "Action": action, "IssuedAt": int(time.time()), "Reason": why}
        cmd.update(params)
        try:
            send_command(cmd)
            log.append(f"Sent command '{action}' to {dev} for {ACTION_MINUTES} minutes ({why}).")
        except Exception as e:
            log.append(f"Command '{action}' failed: {type(e).__name__}.")
    return log


def handler(event, context):
    item = json.loads(json.dumps(event), parse_float=Decimal)
    dev, ts = item["DeviceID"], int(item["Timestamp"])
    item["Timestamp"] = ts
    item["ExpiresAt"] = ts + 7 * 86400

    model = get_model(dev)          # learned before this reading is stored
    tele.put_item(Item=item)

    reasons = []
    for f, lim in LIMITS.items():                       # 1) threshold rules
        if float(item[f]) > lim:
            reasons.append(f"{f}={item[f]} exceeds limit {lim}")
    x = [float(item[f]) for f in FEATURES]
    ml_flag, worst = False, 0
    if model:                                           # 2) machine learning anomaly detection
        d2, worst = score(model, x)
        if d2 > ANOMALY_LIMIT:
            ml_flag = True
            reasons.append(
                f"ML anomaly: score {d2:.0f} (limit {ANOMALY_LIMIT:.0f}); most unusual {FEATURES[worst]}="
                f"{x[worst]:g} (learned normal {model[0][worst]:.1f})")
    if not reasons:
        return {"anomaly": False}

    last = alerts.query(KeyConditionExpression=Key("DeviceID").eq(dev),
                        ScanIndexForward=False, Limit=1)["Items"]
    if last and ts - int(last[0]["Timestamp"]) < COOLDOWN_SECS:   # max one alert per device per 15 min
        return {"anomaly": True, "suppressed": True}

    problems = diagnose(x, model, ml_flag, worst)               # 3) problems + recommendations
    actions = act(problems, x, dev)                             # 4) safe automatic actions
    zone = item.get("Zone", "")
    titles = [p["title"] for p in problems]
    recs = [p["recommendation"] for p in problems]

    alerts.put_item(Item={"DeviceID": dev, "Timestamp": ts, "Zone": zone, "Reasons": reasons,
                          "Problems": titles, "Recommendations": recs, "Actions": actions,
                          "ExpiresAt": ts + 30 * 86400})
    body = ([f"Zone: {zone}", f"Device: {dev}", "", "Detected:"] + [f"- {r}" for r in reasons]
            + ["", "Problems and recommended corrective actions:"]
            + [f"- {p['title']}: {p['recommendation']}" for p in problems]
            + ["", "Automatic actions:"] + ([f"- {a}" for a in actions] or ["- none"]))
    sns.publish(TopicArn=os.environ["TOPIC_ARN"],
                Subject=f"Smart Campus alert: {dev} - {titles[0]}"[:100],
                Message="\n".join(body))
    return {"anomaly": True, "suppressed": False, "actions": actions}