from common import message_protocol

_next_client_id = 0

class MessageHandler:

    def __init__(self):
        global _next_client_id
        self._client_id = _next_client_id
        _next_client_id += 1
    
    def serialize_data_message(self, message):
        [fruit, amount] = message
        return message_protocol.internal.serialize(self._client_id, [fruit, amount])

    def serialize_eof_message(self, message):
        return message_protocol.internal.serialize(self._client_id, [])

    def deserialize_result_message(self, message):
        client_id, fields = message_protocol.internal.deserialize(message)
        if client_id != self._client_id:
            return None
        return fields
