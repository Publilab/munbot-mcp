from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class ComplaintModel(BaseModel):
    nombre: str = Field(..., min_length=3, max_length=120)
    mail: EmailStr
    mensaje: str = Field(..., min_length=10)
    categoria: int  # 1 reclamo, 2 denuncia
    departamento: Optional[int] = None  # 1 seguridad, etc. Si falta, se clasifica automáticamente
    prioridad: Optional[int] = 3  # 1 alta, 3 normal, 5 baja (opcional)

class ComplaintOut(BaseModel):
    id: str
    estado: str
    creado_en: str
    mensaje: str
    nombre: str
    categoria: int
    departamento: int
    prioridad: int
