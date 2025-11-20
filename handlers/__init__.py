"""
Пакет обработчиков для телеграм бота
"""

# Импортируем все модули обработчиков
from . import common
from . import topup
from . import supplies
from . import landing
from . import unicalization
from . import pixel
from . import broadcast

# Список всех роутеров для удобного импорта
__all__ = [
    'common',
    'topup',
    'supplies',
    'landing',
    'unicalization',
    'pixel',
    'broadcast'
]

