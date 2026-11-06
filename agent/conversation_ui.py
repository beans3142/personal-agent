"""Owner-only selection when topic routing cannot decide safely."""
import discord


class TopicChoiceView(discord.ui.View):
    def __init__(self,gateway,message,selection):
        super().__init__(timeout=300);self.gateway=gateway;self.message=message;self.submitted=False
        options=[discord.SelectOption(label=r['title'][:90],value=r['id']) for r in selection['choices']]
        options.append(discord.SelectOption(label='새 작업으로 시작',value='new'))
        select=discord.ui.Select(placeholder='이어서 할 작업을 선택하세요',options=options)
        select.callback=self.choose;self.select=select;self.add_item(select)
    async def interaction_check(self,interaction):
        if interaction.user.id==self.gateway.owner_id:return True
        await interaction.response.send_message('본인 대화만 선택할 수 있어요.',ephemeral=True);return False
    async def choose(self,interaction):
        if self.submitted:return await interaction.response.send_message('이미 선택한 작업을 처리 중이에요.',ephemeral=True)
        self.submitted=True;self.stop()
        await interaction.response.edit_message(content='선택한 작업으로 이어서 처리합니다.',view=None)
        original=self.message;chosen=self.select.values[0]
        class Message:
            content='[[topic:'+chosen+']] '+original.content
            def __getattr__(self,name):return getattr(original,name)
        await self.gateway.on_message(Message())
