"""Unified IDOL Local/Cloud and local-only NCM settings and dispatch."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import bpy
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remi',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
remi=importlib.util.module_from_spec(spec);sys.modules['remi']=remi;spec.loader.exec_module(remi);remi.register()
from remi.features.board.jobs import generation_options,Runner
from remi.features.generation.settings import execution_backend
from remi.features.board.assets import Catalog
from remi.features.cloud import generation as cloud
from remi.features.board.settings_sections import settings_items
try:
    context=bpy.context;settings=context.scene.remi_generation
    for key,model,backend in [('IDOL_LOCAL','idol','LOCAL'),('IDOL_CLOUD','idol','API'),('NCM_LOCAL','rand6_100_ncm','LOCAL'),('TRIPOSPLAT_LOCAL','triposplat','LOCAL')]:
        settings.engine=key
        assert (settings.model,execution_backend(settings))==(model,backend)
        assert generation_options(context)['model']==model
    settings.engine='IDOL_CLOUD';settings.use_cloud=True
    assert settings.engine=='IDOL_CLOUD'
    settings.engine='NCM_LOCAL';settings.use_cloud=True
    assert settings.engine=='NCM_LOCAL' and settings.backend=='API'
    settings.engine='IDOL_LOCAL';settings.idol_steps=50
    fields={item.name for item in settings_items(context,'generate3d')}
    assert 'engine' in fields and 'idol_steps' in fields and 'model' not in fields
    settings.engine='NCM_LOCAL';fields={item.name for item in settings_items(context,'generate3d')}
    assert 'idol_steps' not in fields
    with tempfile.TemporaryDirectory() as directory:
        source=Path(directory)/'source.png';source.write_bytes(b'fixture source')
        settings.output=directory;catalog=Catalog();parent=catalog.image(source)
        for engine in ('IDOL_LOCAL','NCM_LOCAL'):
            settings.engine=engine;options=generation_options(context)
            asset=catalog.derive(parent.id,'SPLAT','GENERATE_3D',options);runner=Runner()
            with patch('remi.features.board.jobs.GenerationJob.start',return_value=SimpleNamespace(directory=Path(directory))) as local,patch('remi.features.cloud.generation.start_3d',side_effect=AssertionError('Local model ran in Cloud')):
                runner.start(context,catalog,asset)
            assert local.call_args.kwargs['model']==options['model']
            context.window_manager.remi_generation.busy=False
    # Explicit local 3D selection survives the account/project Cloud backend.
    settings.engine='NCM_LOCAL';settings.backend='API'
    assert generation_options(context)['backend']=='LOCAL'
    settings.engine='IDOL_CLOUD'
    caps={'enabled':True,'operations':{'generate':True},'modelGeneration':{'model':'Idol'}}
    state=context.window_manager.remi_cloud;state.signed_in=True
    with patch.object(cloud,'available',return_value=True),patch.object(cloud.runtime,'status',return_value={'capabilities':caps}):
        cloud.validate_settings(context,'generate',{'model':'idol'})
        try:cloud.validate_settings(context,'generate',{'model':'rand6_100_ncm'})
        except RuntimeError as error:assert 'locally' in str(error)
        else:raise AssertionError('NCM admitted to Cloud')
    caps['modelGeneration']['model']='Remi NCM'
    with patch.object(cloud,'available',return_value=True),patch.object(cloud.runtime,'status',return_value={'capabilities':caps}):
        try:cloud.validate_settings(context,'generate',{'model':'idol'})
        except RuntimeError as error:assert 'advertised IDOL' in str(error)
        else:raise AssertionError('Wrong Cloud model accepted')
    print('IDOL_ENGINE_REGRESSION_OK: IDOL Local/Cloud, NCM local-only, advertised model, frozen local dispatch')
finally:remi.unregister()
