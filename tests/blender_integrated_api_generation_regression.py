"""Same generation controls/settings through Local or API; no real GPU calls."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_cloud_generation import FixtureServer,native_gaussians,png_image
spec=importlib.util.spec_from_file_location('remi',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
remi=importlib.util.module_from_spec(spec);sys.modules['remi']=remi;spec.loader.exec_module(remi)
remi.register()
from remi.features.cloud import tick,Remi_PT_BoardCloud
from remi.features.cloud.generation import ApiGenerationJob,start_image
from remi.integrations.remi_cloud.client import runtime as cloud
from remi.features.images import operators as images
from remi.features.images.gallery import gallery
from remi.features.images.panel import Remi_PT_ImageSettings,draw_create
from remi.features.generation.panel import draw_generation_settings
from remi.features.board.assets import Catalog
from remi.features.board.jobs import Runner,generation_options
from remi.integrations.iris3b.constants import SIZE_BUCKETS

class Layout:
    def __init__(self,log=None):self.log=[] if log is None else log;self.enabled=True;self.scale_y=1;self.alert=False
    def column(self,**kw):return Layout(self.log)
    row=box=column
    def prop(self,owner,name,**kw):self.log.append(('prop',name))
    def label(self,**kw):self.log.append(('label',kw.get('text')))
    def operator(self,name,**kw):self.log.append(('operator',name));return SimpleNamespace()
    def separator(self,**kw):pass
    def popover(self,**kw):self.log.append(('popover',kw['panel']))
    def progress(self,**kw):pass
    def template_icon(self,**kw):pass

server=FixtureServer();root=tempfile.TemporaryDirectory(prefix='remi-integrated-api-')
try:
    context=bpy.context;gs=context.scene.remi_generation;ims=context.scene.remi_image;state=context.window_manager.remi_cloud
    gs.backend='API';gs.model='rand6_100_ncm';gs.seed=77;gs.gaussians='32768';gs.models='';gs.python='';gs.output=str(Path(root.name)/'models')
    ims.models='';ims.output=str(Path(root.name)/'images');ims.model='turbo';ims.prompt='A blue teapot';ims.seed=11;ims.count=2;ims.size='SQUARE'
    state.base_url=server.api;state.max_cost_eur='0.001'
    assert bpy.ops.remi.cloud_login()=={'FINISHED'}
    def wait(predicate):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            tick()
            if predicate():return
            time.sleep(.02)
        raise AssertionError('Integrated API fixture did not complete')
    wait(lambda:state.signed_in and not state.busy)
    # Existing Create button works without local model files or Python setup.
    layout=Layout();draw_create(layout,context)
    assert ('operator','remi.generate_image') in layout.log
    assert ('operator','remi.download_iris') not in layout.log
    layout=Layout();Remi_PT_ImageSettings.draw(SimpleNamespace(layout=layout),context)
    assert layout.log.count(('prop','use_cloud'))==1
    assert ('prop','count') in layout.log and ('prop','seed') in layout.log
    assert ('prop','python') not in layout.log and ('prop','models') not in layout.log
    account=Layout();Remi_PT_BoardCloud.draw(SimpleNamespace(layout=account),context)
    assert not any(name in {'operation','prompt','image','gaussians','width','height'} for kind,name in account.log if kind=='prop')
    assert ('operator','remi.cloud_generate') not in account.log
    # The original image operator dispatches the selected API adapter, not IrisJob.
    captured={}
    operator=SimpleNamespace(_start_modal=lambda ctx,job,status:captured.update(job=job),report=lambda *a:None)
    with patch.dict(SIZE_BUCKETS,{'SQUARE':(256,256)}),patch.object(images.IrisJob,'start',side_effect=AssertionError('API ran local inference')):
        assert images.Remi_OT_GenerateImage.invoke(operator,context,None)=={'RUNNING_MODAL'}
        image_job=captured['job']
        wait(lambda:image_job.poll() is not None)
        assert image_job.result()==image_job.directory
        image_job.cancel()
        assert not cloud.paused, "Releasing a finished API adapter must not pause the account"
        message=images.Remi_OT_GenerateImage._finish(None,SimpleNamespace(job=image_job),context)
    entries=gallery.refresh(ims.output,force=True)
    assert len(entries)==2 and sorted(e.seed for e in entries)==[11,12]
    assert all(e.prompt==ims.prompt and e.model=='turbo' for e in entries)
    sent=[json.loads(r['body']) for r in server.requests if r['path']=='/api/v1/jobs' and r['method']=='POST']
    assert [r['options']['seed'] for r in sent]==[11,12]
    assert all(r['options']['width']==256 and r['options']['height']==256 for r in sent)
    # Same board Generate 3D action snapshots the normal settings and returns a ready card.
    source=Path(root.name)/'source.png';source.write_bytes(png_image());server.artifact=native_gaussians()
    catalog=Catalog();parent=catalog.image(source);options=generation_options(context)
    assert options['backend']=='API' and options['gaussians']==32768 and options['seed']==77
    child=catalog.derive(parent.id,'SPLAT','GENERATE_3D',options)
    # Queued generation keeps its model snapshot even if the current form changes.
    gs.model="triposplat"
    runner=Runner()
    with patch('remi.features.board.jobs.GenerationJob.start',side_effect=AssertionError('API ran local 3D inference')):
        runner.start(context,catalog,child)
        wait(lambda:runner.tick(context,catalog) and child.state in {'READY','ERROR'})
    assert child.state=='READY',child.status
    assert Path(child.artifact).read_bytes()==native_gaussians()
    last=[json.loads(r['body']) for r in server.requests if r['path']=='/api/v1/jobs' and r['method']=='POST'][-1]
    assert last['operation']=='generate' and last['options']=={'seed':77,'gaussianCount':32768}
    assert not context.window_manager.remi_generation.busy
    # Local/API is a choice of execution, not a second generation settings object.
    gs.model="rand6_100_ncm"
    gs.backend='LOCAL';local_layout=Layout();draw_generation_settings(local_layout,context)
    assert ('prop','python') in local_layout.log and ('prop','gaussians') in local_layout.log
    gs.backend='API';before=len(server.jobs)
    with patch.dict(SIZE_BUCKETS,{'SQUARE':(256,256)}):
        ims.model='base'
        try:start_image(context);raise AssertionError('Unsupported API model silently changed')
        except RuntimeError as e:assert 'Turbo' in str(e)
    assert len(server.jobs)==before
    print('REMI_INTEGRATED_API_GENERATION_OK: shared settings, existing image button, image batches/gallery, board 3D queue, API-only dispatch, local setup preserved')
finally:
    cloud.cleanup();server.close();remi.unregister();root.cleanup()
