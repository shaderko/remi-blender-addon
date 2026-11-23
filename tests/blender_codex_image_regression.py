"""Existing composer -> local worker -> project board -> shared upload adapter."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remi',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
remi=importlib.util.module_from_spec(spec);sys.modules['remi']=remi;spec.loader.exec_module(remi);remi.register()
from remi.integrations.codex_images.client import CodexSession
from remi.features.images import operators as images
from remi.features.images.panel import draw_create,Remi_PT_ImageSettings
from remi.features.board import canvas
from remi.features.board.settings_sections import settings_items
from remi.features.cloud import board_sync,projects
from remi.features.images.gallery import gallery

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGO8E6DBgA0wYRUdtBIADsMBZEZMM/8AAAAASUVORK5CYII=')
class Layout:
    def __init__(self,log=None):self.log=[] if log is None else log;self.enabled=True;self.scale_y=1;self.alert=False
    def column(self,**kw):return Layout(self.log)
    row=box=column
    def prop(self,owner,name,**kw):self.log.append(('prop',name))
    def label(self,**kw):self.log.append(('label',kw.get('text')))
    def operator(self,name,**kw):self.log.append(('operator',name));return SimpleNamespace()
    def separator(self,**kw):pass
    def popover(self,**kw):pass
    def progress(self,**kw):pass
    def template_icon(self,**kw):pass


def wait(job):
    deadline=time.monotonic()+15
    while job.poll() is None and time.monotonic()<deadline:time.sleep(.02)
    return job.result()

root=Path(tempfile.mkdtemp(prefix='remi-codex-regression-'))
session=CodexSession()
board=None
try:
    context=bpy.context;scene=context.scene;settings=scene.remi_image
    context.preferences.system.use_online_access=True
    scene.remi_generation.backend='API';settings.models='';scene.remi_generation.python=''
    settings.provider='CODEX';settings.prompt='A synthetic cube';settings.count=2
    settings.output=str(root/'images')
    with context.temp_override(area=context.screen.areas[0]):
        board=canvas.Board(context);canvas.BOARD=board;board.load(context,[])
    scope='https://example.test/api/v1|synthetic-user|synthetic-project'
    scene['remi_cloud_board_scope']=scope
    layout=Layout();draw_create(layout,context)
    assert ('operator','remi.download_iris') not in layout.log
    layout=Layout();Remi_PT_ImageSettings.draw(SimpleNamespace(layout=layout),context)
    assert ('operator','remi.codex_sign_in') in layout.log
    assert ('prop','codex_quality') in layout.log
    assert layout.log.count(('prop','seed')) == 1  # Independent 3D settings
    assert ('prop','models') not in layout.log
    assert not {'model','seed','memory_mode'} & {s.name for s in settings_items(context,'image')}
    # A Cloud project must be ready before any local image request starts.
    captured_errors=[]
    probe=SimpleNamespace(_start_modal=lambda *args:None,report=lambda kind,message:captured_errors.append(message))
    context.window_manager.remi_cloud.signed_in=True
    with patch.object(board_sync,'current',return_value={}):
        assert images.Remi_OT_GenerateImage.invoke(probe,context,None)=={'CANCELLED'}
    assert any('finish connecting' in error for error in captured_errors)
    context.window_manager.remi_cloud.signed_in=False
    # Normal online-access denial stays inside the board settings instead of
    # escaping its modal handler as a Python traceback.
    from remi.features.board.settings_window import REMI_BOARD_OT_settings
    from remi.features.board.settings_sections import Setting
    host=SimpleNamespace(commit=lambda ctx:True,finish=lambda:(_ for _ in ()).throw(AssertionError("Closed settings on denial")),error="")
    context.preferences.system.use_online_access=False
    REMI_BOARD_OT_settings.activate(host,context,("operator",Setting("action","Continue with ChatGPT",operator="remi.codex_sign_in")))
    assert "Allow Online Access" in host.error
    toggle=next(item for item in settings_items(context,'image') if item.name=='use_online_access')
    toggle.assign(True)
    assert bpy.app.online_access
    with patch('remi.features.board.settings_window.bpy.ops',SimpleNamespace(remi=SimpleNamespace(codex_sign_in=lambda *args,**kwargs:{'CANCELLED'}))):
        REMI_BOARD_OT_settings.activate(host,context,("operator",Setting("action","Continue with ChatGPT",operator="remi.codex_sign_in")))
    assert "could not start" in host.error
    # Run the production worker in an actual isolated child. Only upstream
    # authentication and image replies are synthetic; file/protocol code is real.
    fixture=root/'fixture.py'
    fixture.write_text('''import base64,importlib.util,sys,time
from pathlib import Path
root=Path(%r)
sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('worker',root/'worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
class Connection:
    tokens=None
    def sign_in(self,progress):self.tokens={'synthetic':True}
    def authorize(self,progress):
        if self.tokens is None:self.sign_in(progress)
        return {'Authorization':'Bearer synthetic-memory-only'}
worker.Connection=Connection
worker.request_json=lambda *args,**kwargs:{'data':[{'b64_json':%r}]}
worker.main()
''' % (str(ROOT/'integrations/codex_images'),base64.b64encode(PNG).decode()))
    original_popen=subprocess.Popen
    def launch(command,**kwargs):
        return original_popen([*command[:-1],str(fixture)],**kwargs)
    with patch('remi.integrations.codex_images.client.subprocess.Popen',side_effect=launch):
        sign_in=session.start(action='sign_in',output='');wait(sign_in);sign_in.cancel()
        assert session.signed_in
        process=session.process
        first=board.add_pending(settings.prompt);board.add_pending('Second queued prompt')
        board.positions[first]=[320.25,-410.75];board.running=settings.prompt
        captured={}
        operator=SimpleNamespace(_start_modal=lambda ctx,job,status:captured.update(job=job),report=lambda *args:print('CODEX_TEST_REPORT', args))
        with patch.object(images,'codex_session',session),patch.object(images.IrisJob,'start',side_effect=AssertionError('Codex dispatched Iris')),patch('remi.features.cloud.generation.start_image',side_effect=AssertionError('Codex dispatched Cloud compute')):
            result=images.Remi_OT_GenerateImage.invoke(operator,context,None)
        assert result=={'RUNNING_MODAL'}
        job=captured['job'];wait(job)
        assert session.process is process, 'Successful requests must reuse local sign-in'
        message=images.Remi_OT_GenerateImage._finish(None,SimpleNamespace(job=job),context)
        assert 'Codex' in message
        job.cancel()
        cards=list(board.catalog.assets.values());assert len(cards)==2
        assert all(card.kind=='IMAGE' and Path(card.artifact).is_file() for card in cards)
        assert board.positions[cards[0].id]==[320.25,-410.75]
        assert len(board.pending)==1 and board.pending[0]['prompt']=='Second queued prompt'
        assert not board.running
        # The exact existing sync adapter queues both files to this project,
        # with no separate generation request or scene modification.
        value={'base_url':'https://example.test/api/v1','user_id':'synthetic-user','project_id':'synthetic-project','ready':True}
        sync=board_sync.BoardSync(board);sync.scope=scope;commands=[]
        def command(_context,action,**payload):
            commands.append((action,payload));return 'receipt-'+str(len(commands))
        with patch.object(board_sync,'current',return_value=value),patch.object(sync,'command',side_effect=command):sync.collect(context)
        uploads=[p for a,p in commands if a=='asset'];assert len(uploads)==2
        assert all(Path(p['path']).read_bytes()==PNG for p in uploads)
        assert all('Authorization' not in json.dumps(p) for _,p in commands)
        assert len([a for a,_ in commands if a=='positions'])==2
        wm=context.window_manager;wm.remi_image.busy=True
        assert projects.busy(context)
        with patch.object(sync,'switch',wraps=sync.switch):
            assert not sync.switch(context,{**value,'project_id':'other-project'})
        wm.remi_image.busy=False
        # A result that outlives a board/account change must never enter another
        # project, while the completed image remains recoverable on disk.
        later=session.start(action='generate',output=settings.output,prompt='Different cube',count=1)
        later.board=board;later.board_scope=scope;wait(later)
        scene['remi_cloud_board_scope']='different-scope'
        before=len(board.catalog.assets)
        images.Remi_OT_GenerateImage._finish(None,SimpleNamespace(job=later),context)
        assert len(board.catalog.assets)==before and (later.directory/'image_000.png').is_file()
        later.cancel()
        # Worker death/cancellation cannot report a successful image.
        slow=root/'slow.py';slow.write_text('import time;time.sleep(60)')
        session.shutdown()
        def launch_slow(command,**kwargs):return original_popen([*command[:-1],str(slow)],**kwargs)
        with patch('remi.integrations.codex_images.client.subprocess.Popen',side_effect=launch_slow):
            cancelled=session.start(action='generate',output=settings.output,prompt='Cancel',count=1)
        cancelled.cancel()
        assert session.process is None and not session.signed_in
        try:cancelled.result()
        except RuntimeError:pass
        else:raise AssertionError('Cancelled job reported success')
    # Queue context captures the selected images in board order, independently
    # of later selection changes or a delayed start.
    scene['remi_cloud_board_scope']=scope
    from remi.features.images import references as context_images
    sources=list(board.catalog.assets.values())[:2]
    board.selected={sources[1].id,sources[0].id}
    board.queue=[];board.pending=[];board.running=""
    context.window_manager.remi_image.busy=True
    board.composer='Combine image 1 and image 2'
    assert canvas.submit_current(context)
    queued=board.queue[0]
    expected=[asset.id for asset in context_images.selected_images(board)]
    assert [item['id'] for item in queued['references']]==expected and len(expected)==2
    board.selected.clear()
    board.composer='A separate text-only image'
    assert canvas.submit_current(context)
    assert board.queue[1]['references']==[]
    context.window_manager.remi_image.busy=False
    started=[]
    class QueuedGenerate:
        def poll(self):return True
        def __call__(self,*args,**kwargs):
            started.append([item['id'] for item in context_images.for_request(context,board)])
            return {'RUNNING_MODAL'}
    with patch('remi.features.board.canvas.bpy.ops',SimpleNamespace(remi=SimpleNamespace(generate_image=QueuedGenerate()))):
        canvas.start_next(context)
    assert started==[expected] and board.image_request is None
    assert board.running==queued['prompt'] and len(board.queue)==1
    board.running=''
    board.image_request=queued
    assert [item['id'] for item in context_images.for_request(context,board)]==expected
    # A mocked upstream asserts the actual edit endpoint and decodes each input.
    fixture.write_text(fixture.read_text().replace(
        "worker.request_json=lambda *args,**kwargs:",
        "def verify(url,**kwargs):\n    assert url.endswith('/images/edits')\n    assert len(kwargs['data']['images'])==2\n    for image in kwargs['data']['images']:\n        assert base64.b64decode(image['image_url'].split(',',1)[1]).startswith(b'\\x89PNG')\n    return ").replace("worker.main()", "worker.request_json=verify\nworker.main()"))
    codex=CodexSession()
    try:
        with patch('remi.integrations.codex_images.client.subprocess.Popen',side_effect=launch),patch.object(images,'codex_session',codex):
            captured={}
            operator=SimpleNamespace(_start_modal=lambda ctx,job,status:captured.update(job=job),report=lambda *args:print('REFERENCE_REPORT',args))
            assert images.Remi_OT_GenerateImage.invoke(operator,context,None)=={'RUNNING_MODAL'}
        job=captured['job'];wait(job)
        before=set(board.catalog.assets)
        images.Remi_OT_GenerateImage._finish(None,SimpleNamespace(job=job),context)
        output=next(asset for key,asset in board.catalog.assets.items() if key not in before)
        assert output.parent==expected[0] and output.links['image_context']==expected
        assert len(json.loads((job.directory/'meta.json').read_text())['references'])==2
        job.cancel()
    finally:codex.shutdown();board.image_request=None
    # Wrong project, unsupported/missing file, and oversized selections fail.
    board.image_request={**queued,'scope':'other-project'}
    try:context_images.for_request(context,board)
    except ValueError:pass
    else:raise AssertionError('Queued context crossed projects')
    board.image_request=None;board.selected={sources[0].id}
    original=sources[0].artifact;sources[0].artifact=str(root/'missing.png')
    try:
        board.composer='Must stay in composer'
        assert not canvas.submit_current(context)
        assert board.composer=='Must stay in composer'
        assert 'unavailable' in context.window_manager.remi_image.status
    finally:sources[0].artifact=original
    assert scene.remi_generation.backend=='API'
    print('REMI_CODEX_IMAGE_REGRESSION_OK: isolated worker, provider dispatch, pending placement, project uploads, isolation, cancellation')
finally:
    session.shutdown();canvas.BOARD=None
    if board:board.cleanup()
    remi.unregister()
