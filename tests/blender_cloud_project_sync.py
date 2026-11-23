"""Real Blender board import/Apply <-> real local project backend. Synthetic only."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import bpy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remi',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
remi=importlib.util.module_from_spec(spec);sys.modules['remi']=remi;spec.loader.exec_module(remi);remi.register()
from remi.integrations.remi_cloud.client import runtime
from remi.integrations.remi_cloud.protocol import CloudSession
from remi.integrations.remi_cloud.workspace_worker import WorkspaceWorker
from remi.integrations.remi_cloud.sdk.workspace import WorkspaceClient
from remi.features.board import canvas,editing
from remi.features.cloud import board_sync

base=os.environ['REMI_SYNC_TEST_API'];project=os.environ['REMI_SYNC_TEST_PROJECT']
assert base.startswith('http://127.0.0.1:')
root=Path(tempfile.mkdtemp(prefix='remi-blender-project-'))
owner=CloudSession(base);owner._token='fixture_owner_session_12345';owner.status()
member=CloudSession(base);member._token='fixture_member_session_12345';member.status();member.select_project(project)
web=WorkspaceClient(member,root/'web-files')
runtime.workspace_cache=root/'cache';runtime.directory=root/'ipc';runtime.directory.mkdir()
worker=WorkspaceWorker(owner,runtime.directory,runtime.workspace_cache)
original_status=runtime.status
runtime.status=lambda:{'signed_in':True,'user_id':owner.profile['user_id'],'state':'signed_in'}
worker.start()
wm=bpy.context.window_manager;wm.remi_cloud.base_url=base
wm.remi_cloud.project='default'
scene=bpy.context.scene
scene.remi_image.output=str(root/'images');(root/'images').mkdir()
scene.remi_generation.mesh_output=str(root/'mesh');(root/'mesh').mkdir()
with bpy.context.temp_override(area=bpy.context.screen.areas[0]):
 board=canvas.Board(bpy.context);canvas.BOARD=board
 board.load(bpy.context,[])
scene_before={o.name for o in scene.objects}

def wait(predicate,timeout=20):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  board_sync.tick(bpy.context)
  if predicate():return
  time.sleep(.04)
 raise AssertionError({'workspace':runtime.workspace_status(),'notice':board.cloud_sync.notice,'pending':board.cloud_sync.pending})
try:
 wait(lambda:runtime.workspace_status().get('ready'))
 runtime.workspace_command('switch',base,project_id=project)
 wait(lambda:runtime.workspace_status().get('project_id')==project and board.cloud_sync.scope.endswith(project))
 cube=bpy.context.active_object
 assert cube and cube.type=='MESH'
 for o in bpy.context.view_layer.objects:o.select_set(o==cube)
 bpy.context.view_layer.objects.active=cube
 imported=canvas.add_selected_objects(bpy.context)
 assert len(imported)==1
 asset=imported[0];identifier=asset.id
 wait(lambda:bool(board_sync.marker(asset).get('file_id')))
 first_file=board_sync.marker(asset)['file_id'];card_id=board_sync.marker(asset)['card_id']
 remote=web.board();assert next(a for a in remote['inputs'] if a['id']==card_id)['file']['kind']=='mesh'
 assert len(scene.objects)==len(scene_before)
 board.positions[identifier]=[300.125,-420.25];canvas.save_layout(bpy.context)
 wait(lambda:web.board()['cards'].get(card_id)==board_sync.web_position(board.positions[identifier]))
 web.patch({card_id:{'x':456.75,'y':-123.5}})
 wait(lambda:board.positions[identifier]==board_sync.local_position({'x':456.75,'y':-123.5}))
 # Run the real isolated edit and Apply path, not a hand-written upload.
 editing.open_edit_scene(bpy.context,identifier)
 edit=bpy.context.scene
 assert editing.editing_scene(bpy.context) is not None
 board_sync.tick(bpy.context)
 assert bpy.context.scene==edit and board.scene==scene
 for obj in bpy.context.view_layer.objects:
  if obj.type=='MESH':
   for vertex in obj.data.vertices:vertex.co.x*=1.25
   obj.data.update()
 applied=editing.apply_to_card(bpy.context)
 assert applied.id==identifier
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  wait(lambda:board_sync.marker(asset).get('file_id') not in (None,first_file))
 second=board_sync.marker(asset)['file_id'];assert second!=first_file
 assert next(a for a in web.board()['inputs'] if a['id']==card_id)['file']['id']==second
 assert Path(asset.native_artifact).suffix=='.blend' and Path(asset.native_artifact).is_file()
 # Remote member deletion removes the shared board card, never scene objects.
 web.remove(card_id)
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  wait(lambda:identifier not in board.catalog.assets)
 assert {o.name for o in scene.objects}==scene_before
 # Duplicating a shared mesh must create a new shared identity.
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  source=next((a for a in board.catalog.assets.values() if a.kind=='MESH' and a.state=='READY'),None)
  if source is None:
   for obj in scene.objects:obj.select_set(obj==cube)
   bpy.context.view_layer.objects.active=cube
   source=canvas.add_selected_objects(bpy.context)[0]
   wait(lambda:bool(board_sync.marker(source).get('file_id')))
  duplicates=canvas.duplicate_assets(bpy.context,[source.id]);duplicate=duplicates[0]
  wait(lambda:bool(board_sync.marker(duplicate).get('file_id')))
  assert board_sync.marker(duplicate)['card_id']!=board_sync.marker(source)['card_id']
  web.remove(board_sync.marker(duplicate)['card_id'])
  wait(lambda:duplicate.id not in board.catalog.assets)
 # A native Blender Gaussian object uses the same lossless portable Cloud format.
 from remi.integrations.triposplat.splat_data import save,load
 from remi.blender import gaussian_splats
 import numpy as np
 arrays={'positions':np.zeros((3,3),dtype=np.float32),'scales':np.full((3,3),.01,dtype=np.float32),'rotations':np.tile([1,0,0,0],(3,1)).astype(np.float32),'opacities':np.ones(3,dtype=np.float32),'sh_dc':np.zeros((3,3),dtype=np.float32)}
 archive=root/'local-splats.npz';save(archive,arrays)
 bpy.context.window.scene=scene
 splat=gaussian_splats.import_splats(bpy.context,archive)
 for obj in scene.objects:obj.select_set(obj==splat)
 bpy.context.view_layer.objects.active=splat
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  shared=canvas.add_selected_objects(bpy.context)[0]
  wait(lambda:bool(board_sync.marker(shared).get('file_id')))
  shared_file=next(a['file'] for a in web.board()['inputs'] if a['id']==board_sync.marker(shared)['card_id'])
  portable=Path(web.file(shared_file));assert portable.read_bytes().startswith(b'REMIGAUS')
  np.testing.assert_array_equal(load(portable)['positions'],arrays['positions'])
  assert shared_file['kind']=='splat'
  web.remove(board_sync.marker(shared)['card_id'])
  wait(lambda:shared.id not in board.catalog.assets)
 assert splat.name in scene.objects
 # Another member generates an image and 3D source; only pushed state enters
 # this board, and its local runner must never execute either remote job.
 from uuid import uuid4
 image_job=member.api('POST','/jobs',payload={'operation':'generate_image','inputMode':'single','inputFileIds':[],'prompt':'Fixture cube','options':{'seed':0,'width':256,'height':256},'budget':{'currency':'EUR','maxCostMicros':1000000}},headers={'Idempotency-Key':'fixture-'+uuid4().hex})['job']
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  wait(lambda:any(board_sync.marker(a).get('card_id')==image_job['id'] and a.state=='READY' and Path(a.artifact).is_file() for a in board.catalog.assets.values()))
  image_asset=next(a for a in board.catalog.assets.values() if board_sync.marker(a).get('card_id')==image_job['id'])
  assert image_asset.kind=='IMAGE' and Path(image_asset.artifact).is_file()
  three=member.api('POST','/jobs',payload={'operation':'generate','inputMode':'single','inputFileIds':[board_sync.marker(image_asset)['file_id']],'options':{'seed':0,'gaussianCount':32768},'budget':{'currency':'EUR','maxCostMicros':1000000}},headers={'Idempotency-Key':'fixture-'+uuid4().hex})['job']
  wait(lambda:any(board_sync.marker(a).get('card_id')==three['id'] and a.state=='READY' and Path(a.artifact).is_file() for a in board.catalog.assets.values()))
  generated=next(a for a in board.catalog.assets.values() if board_sync.marker(a).get('card_id')==three['id'])
  assert generated.kind=='SPLAT' and generated.parent==image_asset.id
  assert generated.operation=='' and board.runner.job is None
  board.runner.tick(bpy.context,board.catalog);assert board.runner.job is None
  member.api('DELETE','/jobs/'+three['id']);member.api('DELETE','/jobs/'+image_job['id'])
  wait(lambda:generated.id not in board.catalog.assets and image_asset.id not in board.catalog.assets)
 print('BLENDER_PROJECT_SYNC_OK: imported evaluated GLB, local native snapshot, bidirectional exact positions, actual Edit/Apply revision, remote deletion, untouched scene')
finally:
 worker.close();owner.clear();member.clear()
 runtime.status=original_status;runtime.directory=None
 canvas.BOARD=None
 board.cleanup();remi.unregister()
