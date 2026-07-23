"""Устаревшее пространство импортов.

Реализация находится в core.interfaces.telegram. Модуль оставлен только для
совместимости расширений, использовавших core.telegram.profile.
"""

import sys

from core.interfaces.telegram import profile

sys.modules[__name__ + ".profile"] = profile
