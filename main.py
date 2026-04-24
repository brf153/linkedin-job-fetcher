import logging
import sys
import platform
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import chromedriver_autoinstaller
from bs4 import BeautifulSoup
import pandas as pd
import random
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Keywords used to filter job titles — case-insensitive substring match
INTERN_KEYWORDS = ["intern", "apprentice", "trainee", "internship"]

# Only keep jobs posted within this many days
MAX_DAYS_OLD = 30


def _matches_keywords(title: str, keywords: list[str]) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in keywords)


def _is_recent(date_str: str, max_days: int) -> bool:
    try:
        posted = datetime.strptime(date_str, "%Y-%m-%d")
        return datetime.now() - posted <= timedelta(days=max_days)
    except (ValueError, TypeError):
        return True  # include if date can't be parsed


def _extract_description(driver) -> str:
    try:
        desc_elem = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located(
                (By.CLASS_NAME, "show-more-less-html__markup")
            )
        )
        return desc_elem.text.strip()
    except Exception:
        return ""


def scrape_linkedin_jobs(
    job_title: str,
    location: str,
    pages: int = 3,
    keywords: list[str] = None,
    max_days_old: int = MAX_DAYS_OLD,
) -> list:
    if keywords is None:
        keywords = INTERN_KEYWORDS

    chromedriver_autoinstaller.install()

    chrome_options = webdriver.ChromeOptions()
    for option in ["--window-size=1200,1200", "--ignore-certificate-errors"]:
        chrome_options.add_argument(option)

    if platform.system() == "Linux":
        # Required for Chrome in containerized/CI environments (no kernel namespace support)
        for opt in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]:
            chrome_options.add_argument(opt)
    else:
        chrome_options.add_argument("--headless")

    driver = webdriver.Chrome(options=chrome_options)

    driver.get(
        f"https://www.linkedin.com/jobs/search/?f_E=1&origin=JOB_SEARCH_PAGE_JOB_FILTER"
        f"&geoId=102713980&keywords={job_title}&location={location}&refresh=true&sortBy=DD"
    )

    for i in range(pages):
        logging.info(f"Scrolling page {i + 1} of {pages}...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        try:
            element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "/html/body/div[1]/div/main/section[2]/button")
                )
            )
            element.click()
        except Exception:
            logging.info("'Show more' button not found, continuing...")

        time.sleep(random.choice(range(3, 7)))

    jobs = []
    seen_links = set()
    soup = BeautifulSoup(driver.page_source, "html.parser")
    job_listings = soup.find_all(
        "div",
        class_="base-card relative w-full hover:no-underline focus:no-underline base-card--link base-search-card base-search-card--link job-search-card",
    )

    logging.info(f"Found {len(job_listings)} total listings — filtering...")

    try:
        for job in job_listings:
            title = job.find("h3", class_="base-search-card__title")
            company = job.find("h4", class_="base-search-card__subtitle")
            location_elem = job.find("span", class_="job-search-card__location")
            link_elem = job.find("a", class_="base-card__full-link")
            time_elem = job.find("time")

            if not all([title, company, location_elem, link_elem, time_elem]):
                continue

            title_text = title.text.strip()
            company_text = company.text.strip()
            location_text = location_elem.text.strip()
            apply_link = link_elem["href"]
            date_posted = time_elem.get("datetime", "")

            # Skip if doesn't match keyword filter
            if not _matches_keywords(title_text, keywords):
                continue

            # Skip stale postings
            if not _is_recent(date_posted, max_days_old):
                logging.info(f'Skipping stale posting: "{title_text}" ({date_posted})')
                continue

            # Deduplicate by URL
            clean_link = apply_link.split("?")[0]
            if clean_link in seen_links:
                continue
            seen_links.add(clean_link)

            # Navigate to job page and extract description
            driver.get(apply_link)
            time.sleep(random.choice(range(5, 11)))
            description = _extract_description(driver)

            jobs.append(
                {
                    "Company": company_text,
                    "Title": title_text,
                    "Location": location_text,
                    "Link": f"[Apply]({apply_link})",
                    "Date Posted": date_posted,
                    "Description": description[:300] + "..." if len(description) > 300 else description,
                }
            )

            logging.info(f'Scraped "{title_text}" at {company_text} in {location_text}')

    except Exception as e:
        logging.error(f"Error while scraping: {e}")
        return jobs
    finally:
        driver.quit()

    return jobs


def save_job_data(data: list) -> None:
    if not data:
        logging.warning("No jobs to save.")
        return

    data = sorted(data, key=lambda x: x["Date Posted"], reverse=True)
    df = pd.DataFrame(data)

    with open("README.md", "r") as f:
        readme_content = f.read()

    start = readme_content.find("<!--START_SECTION:workfetch-->")
    end = readme_content.find("<!--END_SECTION:workfetch-->")

    if start == -1 or end == -1:
        logging.error("Could not find workfetch section markers in README.md")
        return

    new_content = (
        f"{readme_content[:start]}"
        f"<!--START_SECTION:workfetch-->\n"
        f"{df.to_markdown(index=False)}\n"
        f"{readme_content[end:]}"
    )

    with open("README.md", "w") as f:
        f.write(new_content)

    logging.info(f"Saved {len(data)} jobs to README.md")


if __name__ == "__main__":
    display = None
    # Only use Xvfb on Linux
    if platform.system() == "Linux":
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(800, 800))
            display.start()
        except ImportError:
            logging.warning("pyvirtualdisplay not installed, running without virtual display")
    
    try:
        jobs = scrape_linkedin_jobs("Software Engineer", "India")
        save_job_data(jobs)
    finally:
        if display:
            display.stop()
