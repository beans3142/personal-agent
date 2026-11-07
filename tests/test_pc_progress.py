import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from agent.pc_control import PCRegistry


def enrolled(tmp_path):
    clock=[1000.0]
    r=PCRegistry(tmp_path/'pcs.db',clock=lambda:clock[0])
    pc=r.redeem(r.invite('데스크톱'));r.poll(pc['node'],pc['token'])
    return r,pc,clock


def test_ack_time_and_scoped_cancel_do_not_touch_newer_shutdown(tmp_path):
    r,pc,clock=enrolled(tmp_path)
    old=r.submit(None,'shutdown','one');r.poll(pc['node'],pc['token'])
    r.ack(pc['node'],pc['token'],old['id'],'succeeded')
    assert r.shutdown_progress(old['id'])['remaining']==60
    cancel=r.cancel_shutdown(old['id'],'cancel-one')
    r.poll(pc['node'],pc['token']);r.ack(pc['node'],pc['token'],cancel['id'],'succeeded')
    assert r.shutdown_progress(old['id'])['phase']=='cancelled'
    clock[0]+=1;r.poll(pc['node'],pc['token']);r.submit(None,'shutdown','two')
    with pytest.raises(ValueError):r.cancel_shutdown(old['id'],'late-old-button')


def test_offline_is_never_reported_as_proven_power_off(tmp_path):
    r,pc,clock=enrolled(tmp_path)
    job=r.submit(None,'shutdown','one');r.poll(pc['node'],pc['token']);r.ack(pc['node'],pc['token'],job['id'],'succeeded')
    clock[0]+=61
    state=r.shutdown_progress(job['id'])
    assert state['phase']=='disconnected' and not state['power_off_confirmed']
    assert state['cancellable'] is False
    with pytest.raises(ValueError):r.cancel_shutdown(job['id'],'late')


def test_cancel_queued_job_without_delivering_any_shutdown(tmp_path):
    r,pc,clock=enrolled(tmp_path)
    job=r.submit(None,'shutdown','one')
    r.cancel_shutdown(job['id'],'cancel')
    assert r.poll(pc['node'],pc['token']) is None
    assert r.shutdown_progress(job['id'])['phase']=='cancelled'


def test_status_edits_original_message_and_button_is_owner_only(tmp_path):
    from agent.pc_progress import ShutdownProgressView,refresh_shutdown_message
    async def check():
        r,pc,clock=enrolled(tmp_path);job=r.submit(None,'shutdown','one')
        bot=SimpleNamespace(owner_id=1,pc_registry=r)
        sent=SimpleNamespace(edit=AsyncMock())
        view=ShutdownProgressView(bot,job['id'])
        assert view.is_persistent() and view.children[0].label=='종료 취소'
        wrong=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
        assert await view.interaction_check(wrong) is False
        await refresh_shutdown_message(bot,job['id'],sent)
        assert sent.edit.await_count==1 and '전달 중' in sent.edit.await_args.kwargs['content']
        click=SimpleNamespace(id=5,response=SimpleNamespace(defer=AsyncMock()),message=sent,followup=SimpleNamespace(send=AsyncMock()))
        await view.children[0].callback(click)
        assert '취소 완료' in sent.edit.await_args.kwargs['content']
        assert all(c.disabled for c in sent.edit.await_args.kwargs['view'].children)
    asyncio.run(check())


def test_natural_shutdown_receipt_keeps_managed_status_message():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('progress_worker',Path('deploy/hermes-worker.py'))
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    receipt=worker.owner_control_receipt('mcp__daevis__owner_control',{'action':'pc_shutdown'},
        {'status':'succeeded','tracking':True,'status_message_id':'12345'})
    assert receipt['status_message_id']=='12345' and receipt['tracking'] is True


def test_text_cancel_updates_the_original_status(tmp_path):
    r,pc,clock=enrolled(tmp_path)
    job=r.submit(None,'shutdown','one');r.poll(pc['node'],pc['token']);r.ack(pc['node'],pc['token'],job['id'],'succeeded')
    cancel=r.submit(None,'cancel','text-cancel');r.poll(pc['node'],pc['token']);r.ack(pc['node'],pc['token'],cancel['id'],'succeeded')
    assert r.shutdown_progress(job['id'])['phase']=='cancelled'


def test_persisted_message_can_be_restored_without_a_new_power_command(tmp_path):
    r,pc,clock=enrolled(tmp_path);job=r.submit(None,'shutdown','one')
    r.save_display(job['id'],123,456)
    r=PCRegistry(r.path,clock=lambda:clock[0])
    assert r.displays()==[{'job':job['id'],'channel':'123','message':'456','finished':0}]
    r.finish_display(job['id']);assert r.displays()==[]
    assert r.displays(all_records=True)[0]['message']=='456'
    assert len(r.list_jobs())==1


def test_cancel_delivery_expires_before_original_shutdown_deadline(tmp_path):
    r,pc,clock=enrolled(tmp_path);job=r.submit(None,'shutdown','one')
    r.poll(pc['node'],pc['token']);r.ack(pc['node'],pc['token'],job['id'],'succeeded')
    clock[0]+=50;r.poll(pc['node'],pc['token'])
    cancel=r.cancel_shutdown(job['id'],'near-deadline')
    clock[0]+=6
    assert r.poll(pc['node'],pc['token']) is None
    assert r.job(cancel['id'])['status']=='expired'
    assert r.shutdown_progress(job['id'])['phase']=='cancel_uncertain'
