from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class ComplaintModel(BaseModel):
    nombre: str = Field(..., min_length=3, max_length=120)
    rut: str = Field(..., min_length=7, max_length=15)
    mail: EmailStr
    mensaje: str = Field(..., min_length=10)
    categoria: int  # 1 reclamo, 2 denuncia
    departamento: Optional[int] = Field(None, ge=1, le=8)  # 1-8 según nuevos departamentos
    prioridad: Optional[int] = 3  # 1 alta, 3 normal, 5 baja (opcional)

class ComplaintOut(BaseModel):
    id: str
    estado: str
    creado_en: str
    mensaje: str
    nombre: str
    rut: str
    categoria: int
    departamento: int
    prioridad: int
