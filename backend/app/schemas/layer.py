from pydantic import BaseModel


class LayerInfo(BaseModel):
    id: str
    label: str
    license_class: str
    attribution: str
    tile_url_template: str
