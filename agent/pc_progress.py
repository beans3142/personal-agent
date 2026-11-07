"""One editable Discord receipt per PC shutdown; observation never proves power-off."""
import asyncio
import logging
import discord
from .pc_discord import owner_check

log=logging.getLogger(__name__)
TERMINAL={'cancelled','cancel_uncertain','failed','expired','uncertain','unknown','unverified','disconnected','superseded'}


def progress_text(state):
    label=state['label'];phase=state['phase']
    texts={
        'queued':'⏳ '+label+'에 종료 요청 전달 중…',
        'dispatched':'⏳ '+label+'에서 요청을 확인 중…',
        'waiting':'⏳ '+label+' 종료 대기 · 약 '+str(state['remaining'])+'초 남음',
        'checking':'🔎 '+label+' 종료 유예가 끝났어요. 연결 상태를 확인 중입니다.',
        'cancelled':'✅ '+label+' 종료 취소 완료',
        'cancelling':'⏳ '+label+'에 취소 요청 전달 중… 아직 취소가 확인되지 않았어요.',
        'cancel_uncertain':'⚠️ '+label+' 종료 취소를 확인하지 못했어요. 직접 확인해 주세요.',
        'disconnected':'⚫ '+label+' 연결 끊김 확인\n전원 종료 여부까지 확인된 것은 아닙니다.',
        'still_online':'⚠️ '+label+' 종료 미확인 · PC 연결이 유지되고 있어요. 저장하지 않은 작업 등이 종료를 막았을 수 있습니다.',
        'superseded':'ℹ️ '+label+'에 새 종료 요청이 있어 이 요청의 상태 추적을 마쳤어요.',
        'expired':'⌛ '+label+' 종료 요청 만료 · PC에 전달되지 않았어요.',
        'failed':'❌ '+label+' 종료 요청 처리 실패',
    }
    return texts.get(phase,'⚠️ '+label+' 종료 결과 미확인 · 자동으로 다시 실행하지 않습니다.')


class ShutdownProgressView(discord.ui.View):
    def __init__(self,bot,job_id,enabled=True):
        super().__init__(timeout=None);self.bot=bot;self.job_id=job_id
        button=discord.ui.Button(label='종료 취소',style=discord.ButtonStyle.danger,
            custom_id='daevis_pc_cancel:'+job_id,disabled=not enabled)
        button.callback=self.cancel;self.add_item(button)

    async def interaction_check(self,interaction):return await owner_check(self.bot,interaction)

    async def cancel(self,interaction):
        await interaction.response.defer()
        try:
            await asyncio.to_thread(self.bot.pc_registry.cancel_shutdown,self.job_id,'button-'+str(interaction.id))
        except ValueError:
            await interaction.followup.send('취소 가능한 요청인지 확인되지 않았어요. 이미 종료가 진행 중이거나 새 요청이 있을 수 있습니다.',ephemeral=True)
        await refresh_shutdown_message(self.bot,self.job_id,interaction.message)


async def refresh_shutdown_message(bot,job_id,message,final=False):
    state=await asyncio.to_thread(bot.pc_registry.shutdown_progress,job_id)
    if final and state['phase']=='checking':state={**state,'phase':'still_online','cancellable':False}
    view=ShutdownProgressView(bot,job_id,enabled=state['cancellable'] and not final)
    await message.edit(content=progress_text(state),view=view,allowed_mentions=discord.AllowedMentions.none())
    return state['phase'] in TERMINAL or final


async def watch_shutdown(bot,job_id,message):
    try:
        # Read-only observation; shutdown is never resubmitted by this worker.
        for _ in range(45):
            if await refresh_shutdown_message(bot,job_id,message):break
            await asyncio.sleep(3)
        else:await refresh_shutdown_message(bot,job_id,message,final=True)
        await asyncio.to_thread(bot.pc_registry.finish_display,job_id)
    except discord.NotFound:
        await asyncio.to_thread(bot.pc_registry.finish_display,job_id)
    except Exception:
        log.exception('PC status message update failed; no power action retried')


async def publish_shutdown(bot,job,channel):
    # Deduplicate repeated tool receipts against the durable message binding.
    async with bot.pc_display_lock:
        existing=next((r for r in bot.pc_registry.displays(all_records=True) if r['job']==job['id']),None)
        if existing:return {'status_message_id':existing['message'],'tracking':True}
        state=bot.pc_registry.shutdown_progress(job['id'])
        sent=await channel.send(progress_text(state),view=ShutdownProgressView(bot,job['id'],state['cancellable']),
            allowed_mentions=discord.AllowedMentions.none())
        bot.pc_registry.save_display(job['id'],channel.id,sent.id)
        bot.add_view(ShutdownProgressView(bot,job['id']),message_id=sent.id)
        task=asyncio.create_task(watch_shutdown(bot,job['id'],sent))
        bot.pc_progress_tasks.add(task);task.add_done_callback(bot.pc_progress_tasks.discard)
        return {'status_message_id':str(sent.id),'tracking':True}


async def restore_shutdowns(bot):
    await bot.wait_until_ready()
    for r in bot.pc_registry.displays():
        try:
            channel=await bot.fetch_channel(int(r['channel']))
            message=await channel.fetch_message(int(r['message']))
            bot.add_view(ShutdownProgressView(bot,r['job']),message_id=int(r['message']))
            task=asyncio.create_task(watch_shutdown(bot,r['job'],message))
            bot.pc_progress_tasks.add(task);task.add_done_callback(bot.pc_progress_tasks.discard)
        except discord.NotFound:bot.pc_registry.finish_display(r['job'])
        except Exception:log.exception('Could not restore PC status message')
