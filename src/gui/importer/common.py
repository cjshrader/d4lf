import datetime
import functools
import logging
import re
import time
from collections.abc import Callable
from typing import Literal, TypeVar

import httpx
from pydantic_yaml import to_yaml_str
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chromium.webdriver import ChromiumDriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.wait import WebDriverWait

from src import __version__
from src.config.loader import IniConfigLoader
from src.config.models import BrowserType, ProfileModel
from src.item.data.item_type import ItemType

LOGGER = logging.getLogger(__name__)

D = TypeVar("D", bound=WebDriver | WebElement)
T = TypeVar("T")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}


def extract_digits(text: str) -> int:
    return int("".join([char for char in text if char.isdigit()]))


def fix_weapon_type(input_str: str) -> ItemType | None:
    input_str = input_str.lower()
    if "1h mace" in input_str:
        return ItemType.Mace
    if "2h mace" in input_str:
        return ItemType.Mace2H
    if "1h sword" in input_str:
        return ItemType.Sword
    if "2h sword" in input_str:
        return ItemType.Sword2H
    if "1h axe" in input_str:
        return ItemType.Axe
    if "2h axe" in input_str:
        return ItemType.Axe2H
    if "2h scythe" in input_str:
        return ItemType.Scythe2H
    if "scythe" in input_str:
        return ItemType.Scythe
    if "crossbow" in input_str:
        return ItemType.Crossbow2H
    if "wand" in input_str:
        return ItemType.Wand
    if "staff" in input_str:
        return ItemType.Staff
    if "dagger" in input_str:
        return ItemType.Dagger
    if "bow" in input_str:
        return ItemType.Bow
    return None


def fix_offhand_type(input_str: str, class_str: str) -> ItemType | None:
    input_str = input_str.lower()
    class_str = class_str.lower()
    if "sorc" in class_str:
        return ItemType.Focus
    if "druid" in class_str:
        return ItemType.OffHandTotem
    if "necro" in class_str:
        if "focus" in input_str or ("offhand" in input_str and "cooldown reduction" in input_str):
            return ItemType.Focus
        if "shield" in input_str:
            return ItemType.Shield
    return None


def format_number_as_short_string(n: int) -> str:
    result = n / 1_000_000
    return f"{int(result)}M" if result.is_integer() else f"{result:.2f}M"


def get_class_name(input_str: str) -> str:
    input_str = input_str.lower()
    if "barbarian" in input_str:
        return "Barbarian"
    if "druid" in input_str:
        return "Druid"
    if "necromancer" in input_str:
        return "Necromancer"
    if "rogue" in input_str:
        return "Rogue"
    if "sorcerer" in input_str:
        return "Sorcerer"
    LOGGER.error(f"Couldn't match class name {input_str=}")
    return "Unknown"


def get_with_retry(url: str) -> httpx.Response:
    for _ in range(5):
        r = httpx.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r
        LOGGER.debug(f"Request {url} failed with status code {r.status_code}, retrying...")
    else:
        LOGGER.error(msg := f"Failed to get a successful response after 5 attempts: {url=}")
        raise ConnectionError(msg)


def handle_popups(driver: ChromiumDriver, method: Callable[[D], Literal[False] | T], timeout=10):
    LOGGER.info("Handling cookie / adblock popups")
    wait = WebDriverWait(driver, timeout)
    for _ in range(3):
        try:
            elem = wait.until(method)
        except TimeoutException:
            break
        elem.click()
        time.sleep(1)


def match_to_enum(enum_class, target_string: str, check_keys: bool = False):
    target_string = target_string.casefold().replace(" ", "").replace("-", "")
    for enum_member in enum_class:
        if enum_member.value.casefold().replace(" ", "").replace("-", "") == target_string:
            return enum_member
        if check_keys and enum_member.name.casefold().replace(" ", "").replace("-", "") == target_string:
            return enum_member
    return None


def retry_importer(func=None, inject_webdriver: bool = False):
    def decorator_retry_importer(wrap_function):
        @functools.wraps(wrap_function)
        def wrapper(*args, **kwargs):
            if inject_webdriver and "driver" not in kwargs and not args:
                kwargs["driver"] = setup_webdriver()
            for _ in range(5):
                try:
                    res = wrap_function(*args, **kwargs)
                    if inject_webdriver:
                        kwargs["driver"].quit()
                    return res
                except Exception:
                    LOGGER.exception("An error occurred while importing. Retrying...")
            return None

        return wrapper

    return decorator_retry_importer if func is None else decorator_retry_importer(func)


def save_as_profile(file_name: str, profile: ProfileModel, url: str):
    file_name = file_name.replace("'", "")
    file_name = re.sub(r"\W", "_", file_name)
    file_name = re.sub(r"_+", "_", file_name).rstrip("_")
    save_path = IniConfigLoader().user_dir / f"profiles/{file_name}.yaml"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as file:
        file.write(f"# {url}\n")
        file.write(f"# {datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")} (v{__version__})\n")
        file.write(
            to_yaml_str(
                profile,
                default_flow_style=None,
                exclude_unset=not IniConfigLoader().general.full_dump,
                exclude=["name", "Sigils", "Uniques"],
            )
        )
    LOGGER.info(f"Created profile {save_path}")


def setup_webdriver() -> ChromiumDriver:
    match IniConfigLoader().general.browser:
        case BrowserType.edge:
            options = webdriver.EdgeOptions()
            options.add_argument("--headless=new")
            options.add_argument("log-level=3")
            driver = webdriver.Edge(options=options)
        case BrowserType.chrome:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("log-level=3")
            driver = webdriver.Chrome(options=options)
        case BrowserType.firefox:
            options = webdriver.FirefoxOptions()
            options.add_argument("--headless")
            options.add_argument("log-level=3")
            driver = webdriver.Firefox(options=options)
    return driver  # noqa # It must be one of the 3 browsers due to ini validation
