import math

import numpy as np
import torch


def decode_camera(camera_latent: torch.Tensor) -> dict:
    """camera_latent [5] -> yaw and pitch (radians), radius and vertical field of view (degrees)."""
    direction, inverse_distance, half_width = camera_latent[:3].float(), float(camera_latent[3]), float(camera_latent[4])
    norm = float(torch.norm(direction))
    dx, dy, dz = (direction / norm).tolist() if norm > 1e-6 else (0.0, 0.0, 1.0)
    if inverse_distance < 1e-3:   # orthographic
        radius = 1000.0
        fov = math.degrees(2 * math.atan(half_width / radius))
    else:
        radius = 1.0 / inverse_distance
        fov = math.degrees(2 * math.atan(half_width * inverse_distance))
    return {'yaw': math.atan2(dx, dy), 'pitch': math.asin(float(np.clip(dz, -1.0, 1.0))), 'radius': radius, 'fov': fov}
