"""Owner-only Discord PC controls; no model-generated targets or commands."""
from types import SimpleNamespace
import discord


async def owner_check(bot,interaction):
    if interaction.user.id==bot.owner_id:return True
    await interaction.response.send_message('본인만 사용할 수 있는 PC 관리입니다.',ephemeral=True)
    return False


class InteractionChannel:
    def __init__(self,interaction):self.interaction=interaction;self.id=getattr(interaction,'channel_id',None)
    async def send(self,text,**kwargs):
        kwargs.setdefault('allowed_mentions',discord.AllowedMentions.none())
        return await self.interaction.followup.send(text,ephemeral=True,wait=True,**kwargs)


class ShutdownConfirmView(discord.ui.View):
    def __init__(self,bot,pc):
        super().__init__(timeout=120);self.bot=bot;self.pc=pc;self.used=False
        for label,yes in [('예',True),('아니오',False)]:
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.danger if yes else discord.ButtonStyle.secondary)
            async def choose(interaction,confirmed=yes):
                if self.used:
                    return await interaction.response.edit_message(content='이미 처리한 확인입니다.',view=None)
                self.used=True
                await interaction.response.edit_message(content='종료 요청을 확인하고 있어요.' if confirmed else '종료하지 않았어요.',view=None)
                self.stop()
                if not confirmed:return
                current=next((p for p in bot.pc_registry.inventory() if p['node']==pc['node'] and p['label']==pc['label']),None)
                if not current or not current['online']:
                    return await interaction.followup.send('PC 연결 프로그램이 응답하지 않거나 등록이 바뀌어 종료하지 않았어요.',ephemeral=True)
                message=SimpleNamespace(id=interaction.id,guild=None,channel=InteractionChannel(interaction))
                try:await bot.handle_pc(message,'shutdown',pc['label'])
                except ValueError:await interaction.followup.send('연결 상태가 바뀌어 종료 요청을 보내지 못했어요.',ephemeral=True)
                except Exception:await interaction.followup.send('요청 결과를 확인하지 못했어요. 다시 실행하기 전에 상태 확인이 필요해요.',ephemeral=True)
            button.callback=choose;self.add_item(button)
    async def interaction_check(self,interaction):return await owner_check(self.bot,interaction)


async def confirm_shutdown(bot,channel,label):
    pc=bot.pc_registry.resolve(label)
    current=next((p for p in bot.pc_registry.inventory() if p['node']==pc['node']),None)
    if not current or not current['online']:
        await channel.send(pc['label']+'의 연결 프로그램이 응답하지 않아요. PC 전원 상태는 확인할 수 없고, 종료 요청은 보내지 않았어요.')
        return {'status':'disconnected','label':pc['label'],'executed':False}
    await channel.send(pc['label']+'의 연결 프로그램이 응답하고 있어요. 이 PC를 60초 뒤에 끌까요?',view=ShutdownConfirmView(bot,pc))
    return {'status':'confirmation_required','label':pc['label'],'executed':False}


class AddPCModal(discord.ui.Modal,title='PC 추가'):
    label=discord.ui.TextInput(label='PC 이름',placeholder='예: 노트북',max_length=24)
    def __init__(self,bot):super().__init__();self.bot=bot
    async def on_submit(self,interaction):
        if not await owner_check(self.bot,interaction):return
        await interaction.response.defer(ephemeral=True,thinking=True)
        message=SimpleNamespace(id=interaction.id,guild=None,channel=InteractionChannel(interaction))
        try:
            await self.bot.handle_pc(message,'register',str(self.label))
            await interaction.followup.send('등록 명령을 본인 DM으로 보냈어요. 새로 추가할 PC에서 한 번 실행하면 됩니다.',ephemeral=True)
        except ValueError:
            await interaction.followup.send('이미 등록된 이름이거나 사용할 수 없는 이름입니다. PC 목록을 확인해 주세요.',ephemeral=True)
        except Exception:
            await interaction.followup.send('등록 명령을 DM으로 보내지 못했어요. 봇의 개인 메시지 허용 상태를 확인해 주세요.',ephemeral=True)


class PCHomeView(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=None);self.bot=bot
        button=discord.ui.Button(label='PC 관리 열기',style=discord.ButtonStyle.primary,custom_id='daevis_pc_open')
        button.callback=self.open;self.add_item(button)
    async def interaction_check(self,interaction):return await owner_check(self.bot,interaction)
    async def open(self,interaction):
        view=PCView(self.bot)
        await interaction.response.send_message(view.content(),view=view,ephemeral=True)


class PCView(discord.ui.View):
    def __init__(self,bot,selected=None):
        super().__init__(timeout=300);self.bot=bot
        self.pcs=bot.pc_registry.inventory()
        current=next((p for p in self.pcs if p['label']==selected),None) or next((p for p in self.pcs if p['is_default']),None)
        self.selected=current['label'] if current else None
        self.selected_node=current['node'] if current else None
        if self.pcs:
            select=discord.ui.Select(placeholder='관리할 PC 선택',row=0,options=[
                discord.SelectOption(label=p['label']+' · '+('연결됨' if p['online'] else '연결 안 됨'),
                                     value=p['label'],default=p['label']==self.selected) for p in self.pcs])
            async def select_pc(interaction):
                view=PCView(bot,select.values[0]);await interaction.response.edit_message(content=view.content(),view=view)
            select.callback=select_pc;self.add_item(select)
        for label,action,style in [('깨우기','wake',discord.ButtonStyle.secondary),
                                  ('60초 뒤 종료','shutdown',discord.ButtonStyle.danger),
                                  ('종료 취소','cancel',discord.ButtonStyle.secondary),
                                  ('기본 PC 설정','default',discord.ButtonStyle.secondary)]:
            disabled=self.selected is None or (action in ('shutdown','cancel') and not current['online'])
            button=discord.ui.Button(label=label,style=style,row=1,disabled=disabled)
            async def perform(interaction,chosen=action):
                await interaction.response.defer(ephemeral=True,thinking=True)
                message=SimpleNamespace(id=interaction.id,guild=None,channel=InteractionChannel(interaction))
                try:
                    self.assert_target()
                    await self.bot.handle_pc(message,chosen,self.selected)
                except ValueError:
                    await interaction.followup.send('대상 PC가 연결되지 않았거나 등록이 바뀌었어요. 새로고침해 주세요.',ephemeral=True)
                except Exception:
                    await interaction.followup.send('요청 결과를 확인하지 못했어요. 확인 전에는 다시 실행하지 마세요.',ephemeral=True)
            button.callback=perform;self.add_item(button)
        add=discord.ui.Button(label='PC 추가',style=discord.ButtonStyle.primary,row=2)
        async def add_pc(interaction):await interaction.response.send_modal(AddPCModal(bot))
        add.callback=add_pc;self.add_item(add)
        refresh=discord.ui.Button(label='새로고침',row=2)
        async def refresh_pc(interaction):
            view=PCView(bot,self.selected);await interaction.response.edit_message(content=view.content(),view=view)
        refresh.callback=refresh_pc;self.add_item(refresh)
        remove=discord.ui.Button(label='등록 해제',row=2,disabled=self.selected is None)
        async def remove_pc(interaction):
            await interaction.response.defer(ephemeral=True,thinking=True)
            message=SimpleNamespace(id=interaction.id,guild=None,channel=InteractionChannel(interaction))
            try:
                self.assert_target()
                await bot.handle_pc(message,'remove',self.selected)
            except ValueError:await interaction.followup.send('이미 해제된 PC입니다. 새로고침해 주세요.',ephemeral=True)
        remove.callback=remove_pc;self.add_item(remove)

    async def interaction_check(self,interaction):return await owner_check(self.bot,interaction)
    def assert_target(self):
        if self.selected is None or self.bot.pc_registry.resolve(self.selected)['node']!=self.selected_node:
            raise ValueError('PC registration changed; refresh required')
    def content(self):
        return '**PC 관리**\n대상: **'+(self.selected or '기본 PC를 선택해 주세요')+'**\n종료는 60초 유예 후 강제 종료 없이 요청합니다. 비서 서버 전원과 별개입니다.'
