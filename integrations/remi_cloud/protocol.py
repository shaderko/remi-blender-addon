"""Thin Blender identity adapter over the shared embedded Remi SDK."""
if __package__:
    from .sdk.embedded import CloudSession as SdkSession, CloudError, api_url, DEFAULT_API
else:
    from sdk.embedded import CloudSession as SdkSession, CloudError, api_url, DEFAULT_API


class CloudSession(SdkSession):
    def __init__(self, base_url=DEFAULT_API):
        super().__init__(base_url, client_id="remi-blender")
