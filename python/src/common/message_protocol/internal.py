import json

def serialize(client_id, message):
    return json.dumps([client_id, message]).encode("utf-8")

def deserialize(raw):
    parsed = json.loads(raw.decode("utf-8"))
    client_id = parsed[0]
    message = parsed[1] 
    return client_id, message
