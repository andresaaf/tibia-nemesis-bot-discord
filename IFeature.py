import threading

class IFeature:
    def __init__(self, client):
        self.client = client

    async def cleanup(self):
        pass

    async def on_ready(self):
        pass

    async def on_message(self, message):
        pass

    async def on_message_edit(self, before, after):
        pass

    async def on_voice_state_update(self, member, before, after):
        pass

    async def on_raw_reaction_add(self, paylod):
        pass
    async def on_reaction_add(self, reaction, user):
        pass

    async def on_raw_reaction_remove(self, payload):
        pass
    async def on_reaction_remove(self, reaction, user):
        pass