import os
import re
import time
import logging
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import undetected_chromedriver as uc

logger = logging.getLogger("scraper")

MEAL_PLAN_URL = "https://mealplans.nyu.edu/"


class NYUMealScraper:
    _driver = None
    _balances_url = None
    _current_skey = None

    def __init__(self):
        self.netid = os.getenv("NYU_NETID")
        self.password = os.getenv("NYU_PASSWORD")
        self.profile_dir = os.path.abspath(os.getenv("CHROME_PROFILE_DIR", "./chrome_profile"))
        self.headless = os.getenv("HEADLESS", "true").lower() == "true"

    def _create_driver(self):
        options = uc.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        driver = uc.Chrome(options=options, version_main=145)
        driver.set_page_load_timeout(30)
        return driver

    def _get_driver(self):
        if NYUMealScraper._driver is None:
            logger.info("Creating new persistent browser instance")
            NYUMealScraper._driver = self._create_driver()
        return NYUMealScraper._driver

    def _kill_driver(self):
        if NYUMealScraper._driver:
            try:
                NYUMealScraper._driver.quit()
            except Exception:
                pass
            NYUMealScraper._driver = None
            NYUMealScraper._balances_url = None
            NYUMealScraper._current_skey = None

    def _needs_login(self, driver) -> bool:
        url = driver.current_url.lower()
        if "mealplans.nyu.edu" in url and ("skey" in url or "index.php" in url or "textpage.php" in url):
            return False
        page_text = driver.page_source.lower()
        return (
            any(kw in url for kw in ["shibboleth", "idp", "login.nyu", "auth", "sso", "microsoftonline", "okta"])
            or "sign in with your netid" in page_text
            or "forgotten or expired password" in page_text
            or "pick an account" in page_text
            or "verify your identity" in page_text
        )

    def _handle_sso(self, driver):
        wait = WebDriverWait(driver, 15)
        logger.info(f"SSO login required — current URL: {driver.current_url}")

        time.sleep(2)

        try:
            account_btn = driver.find_element(
                By.XPATH,
                f"//div[contains(text(),'{self.netid}@nyu.edu')]"
                f" | //small[contains(text(),'{self.netid}@nyu.edu')]"
                f" | //span[contains(text(),'{self.netid}@nyu.edu')]"
            )
            account_btn.click()
            logger.info("Clicked existing account on 'Pick an account' screen")
            time.sleep(3)

            try:
                password_field = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((
                    By.CSS_SELECTOR, "input[type='password'], input[name='passwd'], #i0118, #passwordInput"
                )))
                password_field.clear()
                password_field.send_keys(self.password)
                time.sleep(1)

                for _ in range(3):
                    try:
                        sign_in_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'], #idSIButton9")
                        sign_in_btn.click()
                        logger.info("Password submitted")
                        break
                    except Exception:
                        time.sleep(1)
            except TimeoutException:
                logger.info("No password field after account pick — checking for MFA prompt")

        except Exception:
            logger.info("No account picker — using standard login flow")

            email_field = wait.until(EC.element_to_be_clickable((
                By.CSS_SELECTOR, "input[type='email'], input[name='loginfmt'], #i0116"
            )))
            email_field.clear()
            email_field.send_keys(f"{self.netid}@nyu.edu")
            time.sleep(1)

            for _ in range(3):
                try:
                    next_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'], #idSIButton9")
                    next_btn.click()
                    logger.info("Email submitted")
                    break
                except Exception:
                    time.sleep(1)

            time.sleep(3)

            password_field = wait.until(EC.element_to_be_clickable((
                By.CSS_SELECTOR, "input[type='password'], input[name='passwd'], #i0118, #passwordInput"
            )))
            password_field.clear()
            password_field.send_keys(self.password)
            time.sleep(1)

            for _ in range(3):
                try:
                    sign_in_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'], #idSIButton9")
                    sign_in_btn.click()
                    logger.info("Password submitted")
                    break
                except Exception:
                    time.sleep(1)

        time.sleep(2)
        try:
            mfa_btn = driver.find_element(
                By.XPATH,
                "//div[contains(text(),'Approve with MFA')]"
                " | //div[contains(text(),'Duo')]"
                " | //span[contains(text(),'Approve with MFA')]"
                " | //div[contains(@data-value,'PhoneAppNotification')]"
                " | //div[contains(@data-value,'DuoMfa')]"
            )
            mfa_btn.click()
            logger.info("Clicked 'Approve with MFA (Duo)'")
            time.sleep(3)
        except Exception:
            logger.info("No MFA approval button found")

        time.sleep(3)
        try:
            stay_btn = driver.find_element(By.CSS_SELECTOR, "#idSIButton9, input[value='Yes']")
            stay_btn.click()
            logger.info("Clicked 'Stay signed in'")
        except Exception:
            logger.info("No 'Stay signed in' prompt")

    def _extract_skey(self, url) -> str | None:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return params.get("skey", [None])[0]

    def _navigate_to_balances(self, driver):
        current_url = driver.current_url
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        skey = params.get("skey", [None])[0]
        cid = params.get("cid", [None])[0]

        if skey and cid:
            # Log skey change
            old_skey = NYUMealScraper._current_skey
            if old_skey and old_skey != skey:
                logger.info(f"skey CHANGED: {old_skey[:8]}... → {skey[:8]}...")
            elif old_skey == skey:
                logger.warning(f"skey UNCHANGED: {skey[:8]}... (balance may be stale)")
            else:
                logger.info(f"skey set: {skey[:8]}...")
            NYUMealScraper._current_skey = skey

            balances_url = f"https://mealplans.nyu.edu/index.php?skey={skey}&cid={cid}"
            NYUMealScraper._balances_url = balances_url
            driver.get(balances_url)
            logger.info(f"Navigated to Balances: {balances_url}")
            time.sleep(3)
        else:
            balances_link = driver.find_element(By.XPATH, "//*[contains(text(),'BALANCES')]")
            balances_link.click()
            logger.info("Clicked BALANCES link")
            time.sleep(3)

    def _extract_swipe_count_from_text(self, text) -> int | None:
        patterns = [
            r"Swipe it Forward Bank.*?Current Balance\s*(\d+)",
            r"Current Balance\s+(\d+)\b",
            r"(\d+)\s*meal(?:s)?\s*(?:remaining|left|available)",
            r"meal(?:s)?[:\s]+(\d+)",
            r"swipe(?:s)?[:\s]+(\d+)",
            r"(\d+)\s*swipe(?:s)?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                count = int(match.group(1))
                logger.info(f"Found swipe count via regex: {count}")
                return count

        logger.warning(f"Could not extract swipe count. Text preview:\n{text[:1000]}")
        return None

    def get_swipe_count(self) -> dict:
        try:
            driver = self._get_driver()

            # Subsequent polls: revisit portal to get fresh skey
            if NYUMealScraper._balances_url:
                logger.info("Refreshing — getting fresh session key...")
                driver.get(MEAL_PLAN_URL)
                time.sleep(5)

                if self._needs_login(driver):
                    logger.warning("Session expired — need to re-login")
                    NYUMealScraper._balances_url = None
                    NYUMealScraper._current_skey = None
                else:
                    try:
                        self._navigate_to_balances(driver)
                    except Exception as e:
                        logger.warning(f"Could not navigate to Balances: {e}")

                    body = driver.find_element(By.TAG_NAME, "body").text
                    count = self._extract_swipe_count_from_text(body)
                    return {
                        "swipe_count": count,
                        "authenticated": True,
                        "error": None if count is not None else "Could not find swipe count",
                        "page_url": driver.current_url,
                        "skey_changed": NYUMealScraper._current_skey != self._extract_skey(driver.current_url) if self._extract_skey(driver.current_url) else None,
                    }

            # First run: full login flow
            driver.get(MEAL_PLAN_URL)

            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(5)

            if self._needs_login(driver):
                self._handle_sso(driver)
                time.sleep(3)

                logger.info("Waiting for MFA completion (enter Windows Hello PIN if prompted)...")
                WebDriverWait(driver, 60).until(
                    lambda d: "mealplans" in d.current_url.lower()
                )
                logger.info(f"Login complete — landed on {driver.current_url}")

            try:
                self._navigate_to_balances(driver)
            except Exception as e:
                logger.warning(f"Could not navigate to Balances: {e}")

            driver.save_screenshot("debug_screenshot.png")
            logger.info(f"Current URL: {driver.current_url}")
            logger.info(f"Page title: {driver.title}")

            body = driver.find_element(By.TAG_NAME, "body").text
            count = self._extract_swipe_count_from_text(body)

            return {
                "swipe_count": count,
                "authenticated": True,
                "error": None if count is not None else "Could not find swipe count",
                "page_url": driver.current_url,
            }

        except Exception as e:
            logger.exception("Scraper failed")
            self._kill_driver()
            return {
                "swipe_count": None,
                "authenticated": False,
                "error": str(e),
                "page_url": None,
            }
