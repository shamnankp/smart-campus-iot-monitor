import json, random, sys, time
from awscrt import mqtt
from awsiot import mqtt_connection_builder

endpoint = sys.argv[1]
args = sys.argv[2:]
def option(name):
    return args[args.index(name) + 1] if name in args else None

anomalies = "--anomalies" in args
offline = option("--offline")        # node that stops sending (failed sensor)
poor_air = option("--poor-air")      # node whose CO2 keeps rising until ventilation is boosted
hvac_waste = option("--hvac-waste")  # node whose HVAC wastes energy until eco mode is applied
TOPIC = "campus/building/sensors"
COMMANDS = "campus/building/commands"
NODES = {"node-library": ("Library", 12), "node-lab": ("Computer Lab", 22),
         "node-hostel": ("Hostel Block", 18), "node-admin": ("Admin Block", 15),
         "node-canteen": ("Canteen", 25)}   # (zone, base kW)

state = {n: {"co2": 500.0, "vent_until": 0.0, "eco_until": 0.0} for n in NODES}

def make_handler(name):
    def on_command(topic, payload, dup=False, qos=None, retain=False, **kwargs):
        try:
            cmd = json.loads(payload)
        except ValueError:
            return
        if cmd.get("DeviceID") != name:
            return                                   # command for another node
        if time.time() - cmd.get("IssuedAt", 0) > 60:
            print(f"<< {name}: ignored an old command")          # safety: no replay of stale commands
            return
        secs = min(int(cmd.get("Minutes", 15)), 30) * 60          # safety: never longer than 30 minutes
        action = cmd.get("Action")
        if action == "ventilation_boost":
            state[name]["vent_until"] = time.time() + secs
        elif action == "hvac_eco_mode" and 22 <= cmd.get("Setpoint", 0) <= 26:   # safety: setpoint bounds
            state[name]["eco_until"] = time.time() + secs
        else:
            print(f"<< {name}: rejected command {cmd}")
            return
        print(f"<< COMMAND received by {name}: {action} for {secs // 60} min")
    return on_command

def connect(name):
    c = mqtt_connection_builder.mtls_from_path(
        endpoint=endpoint, client_id=name,
        cert_filepath=f"certs/{name}.cert.pem",
        pri_key_filepath=f"certs/{name}.private.key",
        ca_filepath="certs/AmazonRootCA1.pem",
        clean_session=False, keep_alive_secs=30)
    c.connect().result()
    c.subscribe(topic=COMMANDS, qos=mqtt.QoS.AT_LEAST_ONCE, callback=make_handler(name))[0].result()
    return c

conns = {n: connect(n) for n in NODES}
print("All nodes connected (TLS mutual auth) and listening for commands. Ctrl+C to stop.")

while True:
    now = time.time()
    for name, (zone, base) in NODES.items():
        s = state[name]
        vent, eco = now < s["vent_until"], now < s["eco_until"]
        if poor_air == name:
            s["co2"] = min(s["co2"] + 60, 2200)          # a problem that keeps getting worse
        if vent:
            s["co2"] = 450 + (s["co2"] - 450) * 0.6      # ventilation pulls CO2 down
        elif poor_air != name:
            s["co2"] += (500 - s["co2"]) * 0.1           # otherwise drifts back to normal
        factor = 2.2 if hvac_waste == name else 1.0      # HVAC running when it should not
        if eco:
            factor *= 0.75                               # eco mode saves energy
        if name == offline:          # simulates a failed sensor
            continue
        p = {"DeviceID": name, "Zone": zone, "Timestamp": int(time.time()),
             "Temperature": round(random.gauss(24, 1.5) + (1.0 if eco else 0.0), 1),
             "Humidity": round(random.gauss(55, 4), 1),
             "AirQuality": round(random.gauss(40 + max(0.0, s["co2"] - 500) * 0.08, 8)),
             "CO2": round(random.gauss(s["co2"], 50)),
             "PowerConsumption": round(random.gauss(base * factor, 1.2), 1),
             "Ventilation": "boost" if vent else "normal",
             "HvacMode": "eco" if eco else "normal"}
        if anomalies and random.random() < 0.005:
            kind = random.choice(["power", "co2", "aqi"])
            if kind == "power": p["PowerConsumption"] = round(base * 3, 1)
            if kind == "co2":   p["CO2"] = 1800
            if kind == "aqi":   p["AirQuality"] = 190
            print(f"!! injected {kind} anomaly on {name}")
        conns[name].publish(topic=TOPIC, payload=json.dumps(p), qos=mqtt.QoS.AT_LEAST_ONCE)
        tag = (" [ventilation boost]" if vent else "") + (" [eco mode]" if eco else "")
        print("sent", name, p["PowerConsumption"], "kW", p["CO2"], "ppm" + tag)
    time.sleep(10)