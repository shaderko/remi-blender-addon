"""Model-tuned Gaussian-spacing threshold. No world-axis selection or fitted surface."""
from dataclasses import dataclass,asdict
import time
import numpy as np
from scipy.spatial import cKDTree
from scipy import ndimage as ndi
@dataclass(frozen=True)
class Config:
 level:float=.05
 minimum_level:float=.00001
 power:float=20.
 reference_percentile:float=85.
 neighbors:int=8
 stride:int=2
 opacity_min:float=.02
 batch:int=16384
 opacity_guard_low:float=.3
 opacity_guard_high:float=.7

 def __post_init__(self):
  if not np.isfinite([self.level,self.minimum_level,self.power,self.reference_percentile]).all():raise ValueError('Nonfinite threshold configuration')
  if not 0<self.minimum_level<=self.level<1 or self.power<0:raise ValueError('Invalid level or power')
  if not 0<=self.opacity_guard_low<self.opacity_guard_high<=1:raise ValueError('Invalid opacity guard')
  if not 0<=self.reference_percentile<=100 or self.neighbors<1 or self.stride<1 or self.batch<1:raise ValueError('Invalid support configuration')

class GaussianSpacing:
 def __init__(self,splats,config=Config()):
  self.config=config;active=splats.opacities>=config.opacity_min;p=splats.positions[active]
  if len(p)<=config.neighbors:raise ValueError('Too few active Gaussians')
  self.opacity=splats.opacities[active];self.tree=cKDTree(p);d,_=self.tree.query(p,k=config.neighbors+1,workers=1);self.spacing=np.maximum(d[:,-1],1e-12);self.reference=float(np.percentile(self.spacing,config.reference_percentile))
 def query_log(self,query):
  _,ids=self.tree.query(query,k=self.config.neighbors,workers=1)
  if self.config.neighbors==1:ids=ids[:,None]
  local=np.median(self.spacing[ids],axis=1)
  opacity=np.median(self.opacity[ids],axis=1);guard=np.clip((opacity-self.config.opacity_guard_low)/(self.config.opacity_guard_high-self.config.opacity_guard_low),0,1)
  return np.maximum(np.log(local/self.reference),0)*guard
 def query_level(self,query):
  return np.maximum(self.config.minimum_level,self.config.level*np.exp(-self.config.power*self.query_log(query)))

def support_map(field,low,voxel,splats,config=Config()):
 start=time.process_time();support=GaussianSpacing(splats,config);spacing=support.spacing;reference=support.reference
 coarse=field[::config.stride,::config.stride,::config.stride];cells=np.argwhere(coarse>0);logs=np.zeros(coarse.shape,np.float32)
 for begin in range(0,len(cells),config.batch):
  cc=cells[begin:begin+config.batch];q=low+(cc*config.stride+.5)*voxel;logs[tuple(cc.T)]=support.query_log(q)
 # Zero-support cells use nearby Gaussian spacing too: avoid resetting the map
 # to the high threshold inside gaps between occupied coarse samples.
 if len(cells):
  unseen=coarse<=0;_,nearest=ndi.distance_transform_edt(unseen,return_indices=True);logs[unseen]=logs[tuple(nearest[:,unseen])]
 stats={'support_map_cpu_seconds':time.process_time()-start,'reference_spacing':reference,'spacing_percentiles':np.percentile(spacing,[10,25,50,75,90,95]).tolist(),'queries':len(cells),'parameters':asdict(config)}
 return logs,stats

def threshold_field(logs,shape,config=Config()):
 # Interpolate log sparsity, then apply the power. Interpolating raw thresholds
 # would mix dense high levels back into low-support holes.
 fine=ndi.affine_transform(logs,np.eye(3)/config.stride,output_shape=shape,order=1,mode='nearest',prefilter=False)
 return np.maximum(config.minimum_level,config.level*np.exp(-config.power*fine)).astype(np.float32)
