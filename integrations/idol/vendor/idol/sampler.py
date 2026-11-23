import numpy as np
import torch
from tqdm import tqdm


@torch.no_grad()
def sample_flow(model, noise, cond, neg_cond, steps: int = 50, rescale_t: float = 3.0, cfg_strength: float = 3.0,
                verbose: bool = True, batch_cfg: bool = False):
    """Integrate from noise (t = 1) to data (t = 0) in ``steps`` Euler steps.

    rescale_t shifts the time grid towards t = 1 (more steps at high noise).
    cfg_strength: guided velocity = (1 + w) * v(cond) - w * v(neg_cond).
    batch_cfg: run cond and neg_cond as one batch-2 forward instead of two passes (same formula, faster on GPU)."""

    def velocity(sample, t, condition):
        return model(sample, torch.tensor([1000 * t] * sample.shape[0], device=sample.device, dtype=torch.float32),
                     condition)

    sample = noise
    t_seq = np.linspace(1, 0, steps + 1)
    t_seq = rescale_t * t_seq / (1 + (rescale_t - 1) * t_seq)
    for i in tqdm(range(steps), desc='Sampling', disable=not verbose):
        t, t_prev = t_seq[i], t_seq[i + 1]
        if cfg_strength > 0 and batch_cfg:
            n = sample.shape[0]
            both = velocity(torch.cat([sample, sample]), t, torch.cat([cond, neg_cond]))
            v = (1 + cfg_strength) * both[:n] - cfg_strength * both[n:]
        else:
            v = velocity(sample, t, cond)
            if cfg_strength > 0:
                v = (1 + cfg_strength) * v - cfg_strength * velocity(sample, t, neg_cond)
        sample = sample - v * (t - t_prev)
    return sample
