"""
Сервис для работы с API luboydomen.info
"""
import aiohttp
from config import LUBOYDOMEN_API_TOKEN

LUBOYDOMEN_API_BASE = "https://luboydomen.info/api/ggl"


async def get_all_phone_numbers() -> dict:
    """Получает список всех номеров телефонов из API с учетом пагинации"""
    headers = {"Authorization": f"Token {LUBOYDOMEN_API_TOKEN}"}
    all_numbers = []
    offset = 0
    limit = 100
    total = None

    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(
                f"{LUBOYDOMEN_API_BASE}/numbers",
                headers=headers,
                params={"limit": limit, "offset": offset}
            ) as response:
                result = await response.json()

                if not result.get("success"):
                    return result

                data = result.get("data", {})
                numbers = data.get("numbers", [])
                pagination = data.get("pagination", {})

                all_numbers.extend(numbers)

                if total is None:
                    total = pagination.get("total", len(numbers))

                # Проверяем, есть ли ещё номера
                offset += limit
                if offset >= total or not numbers:
                    break

    return {
        "success": True,
        "data": {
            "numbers": all_numbers,
            "pagination": {
                "total": total or len(all_numbers),
                "limit": limit,
                "offset": 0
            }
        }
    }


async def purchase_number(custom_name: str, country_code: str = "GB", duration_months: int = 1, auto_renew: bool = False) -> dict:
    """Покупает один номер телефона через API"""
    headers = {
        "Authorization": f"Token {LUBOYDOMEN_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "country_code": country_code,
        "duration_months": duration_months,
        "auto_renew": auto_renew,
        "custom_name": custom_name
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{LUBOYDOMEN_API_BASE}/numbers/purchase/",
            headers=headers,
            json=payload
        ) as response:
            return await response.json()


async def get_sms_messages(number_id: str, limit: int = 100, offset: int = 0) -> dict:
    """Получает список SMS для номера по его ID"""
    headers = {"Authorization": f"Token {LUBOYDOMEN_API_TOKEN}"}

    params = {"limit": limit, "offset": offset}

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{LUBOYDOMEN_API_BASE}/numbers/{number_id}/sms",
            headers=headers,
            params=params
        ) as response:
            return await response.json()


