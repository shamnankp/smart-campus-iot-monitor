import json, os, boto3
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb")
tele = ddb.Table(os.environ["TELEMETRY_TABLE"])
alerts = ddb.Table(os.environ["ALERTS_TABLE"])
DEVICES = os.environ["DEVICES"].split(",")

def q(table, dev, n):
    return table.query(KeyConditionExpression=Key("DeviceID").eq(dev),
                       ScanIndexForward=False, Limit=n)["Items"]

def resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=float)}

def handler(event, context):
    path = event["rawPath"]
    qs = event.get("queryStringParameters") or {}
    if path.endswith("/latest"):
        return resp(200, [i for d in DEVICES for i in q(tele, d, 1)])
    if path.endswith("/history"):
        dev = qs.get("deviceId")
        if dev not in DEVICES:
            return resp(400, {"error": "unknown deviceId"})
        return resp(200, q(tele, dev, min(int(qs.get("limit", 60)), 200))[::-1])
    if path.endswith("/alerts"):
        items = [i for d in DEVICES for i in q(alerts, d, 10)]
        return resp(200, sorted(items, key=lambda i: i["Timestamp"], reverse=True)[:20])
    return resp(404, {"error": "not found"})