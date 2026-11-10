"""Preserve our owner-only Discord adapter, use Hermes through isolated MCP workers."""
import logging
import asyncio
import os
from pathlib import Path

from .android_control import AndroidControl, UiaDevice
from .gateway import DATA, ROOT, Gateway
from .hermes_runner import HermesRunner
from .memory import Memory
from .tools import build_tools


class HermesGateway(Gateway):
    async def setup_hook(self):
        # Autoreply is off until a new explicit owner request. Do not revive an
        # old watch window or uncertain pending send when changing runtimes.
        self.loop.create_task(self.schedule_loop())
        self.loop.create_task(self.heartbeat_loop())
        self.loop.create_task(self.idle_shutdown_loop())
        token=os.environ.get('DAEVIS_AGENT_NODE_TOKEN')
        if token:
            # Voice requests from the owner's PCs arrive through the always-on power relay.
            from .pc_control import BASE
            from .voice_agent import agent_loop
            self.loop.create_task(agent_loop(self,BASE,token))

    async def heartbeat_loop(self):
        from .heartbeat import HeartbeatTracker
        from .runtime_health import sample_health
        await self.wait_until_ready()
        tracker=HeartbeatTracker(DATA/'heartbeat.json')
        while not self.is_closed():
            try:
                samples=await asyncio.to_thread(sample_health,DATA,os.environ)
                notices=tracker.update(samples)
                if notices:
                    await self.send_long(await self.owner_dm(),'\n'.join(notices))
            except Exception:
                logging.getLogger('agent').warning('heartbeat sampling or notice delivery failed')
            await asyncio.sleep(60)

    def run_scheduled(self, text):
        import time
        from .gateway import instructions_for, kakao_status_reply
        started=time.monotonic()
        result=self.engine.run(text,instructions_for(self.memory),[],owner_request=False)
        reply=kakao_status_reply(result.text) if result.kakao_used else result.text
        return reply + "\n\n" + result.footer(time.monotonic()-started)


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    memory=Memory(DATA/"agent.db")
    android=AndroidControl(UiaDevice(os.environ.get("ANDROID_SERIAL")),outbox=DATA/"outbox",observe_after=True)
    gateway=HermesGateway(int(os.environ["OWNER_ID"]),None,memory,build_tools(memory,DATA/"schedule.json"),DATA/"schedule.json",None,android)
    from .desktop_text import DesktopText
    from .claude_text import ClaudeText, ChatProviders
    gateway.engine=HermesRunner(os.environ["HERMES_PYTHON"],str(ROOT/"deploy/hermes-worker.py"),data=DATA,
                                claude_token=ClaudeText._token)  # used only after a ChatGPT usage limit
    from .web_llm import QuickLane
    gateway.quick_lane = QuickLane(ChatProviders(DesktopText(), ClaudeText()), enabled=os.environ.get('QUICK_LANE','on') != 'off')
    gateway.run(os.environ["DISCORD_BOT_TOKEN"],log_handler=None)


if __name__=="__main__": main()
