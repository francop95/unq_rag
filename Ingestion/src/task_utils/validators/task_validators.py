"""Validadores para las tareas (Task)"""

from typing import Optional, List


class DocumentExtensionValidator:
    """Valida que el archivo tenga la extensión correcta"""
    
    def __init__(self, field_name: str, expected_extension: str):
        """
        Args:
            field_name: Nombre del campo a validar en input_data
            expected_extension: Extensión esperada (ej: ".pdf", ".docx")
        """
        self.field_name = field_name
        self.expected_extension = expected_extension.lower()
    
    def validate(self, input_data: dict) -> Optional[str]:
        """
        Valida que el archivo en input_data tenga la extensión correcta.
        
        Args:
            input_data: Diccionario con datos de entrada
            
        Returns:
            String con el error si hay, None si es válido
        """
        if self.field_name not in input_data:
            return f"Campo '{self.field_name}' no encontrado en input_data"
        
        file_path = input_data[self.field_name]
        
        if not isinstance(file_path, str):
            return f"'{self.field_name}' debe ser un string (path)"
        
        if not file_path.lower().endswith(self.expected_extension):
            return f"'{self.field_name}' debe tener extensión '{self.expected_extension}', pero tiene: {file_path}"
        
        return None


class TaskSettingPresenceValidator:
    """Valida que las configuraciones necesarias estén presentes en task_settings"""
    
    def __init__(self, required_keys: List[str]):
        """
        Args:
            required_keys: Lista de keys requeridas en task_settings
        """
        self.required_keys = required_keys
    
    def validate(self, task_settings: dict) -> Optional[str]:
        """
        Valida que todas las keys requeridas estén en task_settings.
        
        Args:
            task_settings: Diccionario con configuración
            
        Returns:
            String con el error si hay, None si es válido
        """
        missing_keys = [key for key in self.required_keys if key not in task_settings]
        
        if missing_keys:
            return f"Configuración faltante en task_settings: {', '.join(missing_keys)}"
        
        return None
