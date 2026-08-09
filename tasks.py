import os
import requests
import json
import random
from bs4 import BeautifulSoup
import logging
import time
import itertools
import sys

try:
    import cloudscraper
except ImportError:
    cloudscraper = None

try:
    import curl_cffi.requests as curl_requests
except Exception:
    curl_requests = None

LETTERBOXD_PROXY_URL = os.getenv('LETTERBOXD_PROXY_URL')
COOKIE_JAR_PATH = os.path.join(os.path.dirname(__file__), 'cookie_jar.json')

# Jitter configuration (seconds). Can be overridden via env vars.
JITTER_MIN = float(os.getenv('LB_JITTER_MIN', '1.0'))
JITTER_MAX = float(os.getenv('LB_JITTER_MAX', '5.0'))

# Scraper-specific exceptions
class LetterboxdScrapeBlocked(Exception):
    pass

class LetterboxdScrapeError(Exception):
    pass

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - TASKS.PY - %(message)s')


def create_letterboxd_session():
    if cloudscraper:
        session = cloudscraper.create_scraper()
        logging.info('SESSION: Using cloudscraper to bypass Cloudflare challenges.')
    else:
        session = requests.Session()
        session.trust_env = False
        logging.info('SESSION: cloudscraper missing, using plain requests.Session. This may be blocked by Cloudflare.')

    # A more realistic header set to reduce fingerprinting signals
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://letterboxd.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Sec-CH-UA': '"Chromium";v="120", "Not:A-Brand";v="99"',
        'Sec-CH-UA-Platform': '"Windows"',
        'Sec-CH-UA-Mobile': '?0',
        'Upgrade-Insecure-Requests': '1',
    })

    # Load persisted cookies (if any) so we reuse cf_clearance across runs
    try:
        load_cookies_into_session(session, COOKIE_JAR_PATH)
        logging.info('SESSION: Loaded persisted cookies (if present).')
    except Exception:
        logging.debug('SESSION: No cookie jar loaded (or failed to load).')
    return session


def save_session_cookies(session, path=COOKIE_JAR_PATH):
    try:
        cookies_dict = requests.utils.dict_from_cookiejar(session.cookies)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(cookies_dict, fh)
        logging.info(f'SESSION: Saved {len(cookies_dict)} cookies to {path}')
    except Exception as e:
        logging.warning(f'SESSION: Failed to save cookies to {path}: {e}')


def load_cookies_into_session(session, path=COOKIE_JAR_PATH):
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            cookies_dict = json.load(fh)
        session.cookies.update(cookies_dict)
    except Exception as e:
        logging.warning(f'SESSION: Failed to load cookies from {path}: {e}')

# --- Rate Limiting and Retry Logic ---
REQUESTS_PER_MINUTE_NORMAL = 30   # 1 request every 2 seconds
REQUESTS_PER_MINUTE_AJAX = 20     # 1 request every 3 seconds
SECONDS_PER_MINUTE = 60
last_request_time_normal = 0
last_request_time_ajax = 0

# Store the default empty poster URL
EMPTY_POSTER_URL = "https://s.ltrbxd.com/static/img/empty-poster-150-DtnLDE3k.png"

def rate_limit(is_ajax_call=False):
    global last_request_time_normal, last_request_time_ajax

    if is_ajax_call:
        last_request_time_ref = last_request_time_ajax
        requests_per_minute_ref = REQUESTS_PER_MINUTE_AJAX
        log_prefix = "AJAX Rate limiting"
    else:
        last_request_time_ref = last_request_time_normal
        requests_per_minute_ref = REQUESTS_PER_MINUTE_NORMAL
        log_prefix = "Normal Rate limiting"

    current_time = time.time()
    time_since_last_request = current_time - last_request_time_ref
    required_delay = SECONDS_PER_MINUTE / requests_per_minute_ref
    # Add randomized jitter to make timing less uniform
    jitter = random.uniform(JITTER_MIN, JITTER_MAX)

    if time_since_last_request < required_delay:
        sleep_time = (required_delay - time_since_last_request) + jitter
        logging.info(f"{log_prefix}: Sleeping for {sleep_time:.2f} seconds (including {jitter:.2f}s jitter)")
        time.sleep(sleep_time)
    else:
        # Even if we're past the required delay, add a small jitter to avoid fixed intervals
        logging.info(f"{log_prefix}: No required delay, sleeping jitter {jitter:.2f}s")
        time.sleep(jitter)
    
    if is_ajax_call:
        last_request_time_ajax = time.time()
    else:
        last_request_time_normal = time.time()


def _build_proxy_dict(proxy_url):
    if not proxy_url:
        return None
    return {'http': proxy_url, 'https': proxy_url}


def make_request_with_basic_retry(url, session, is_ajax_call=False, max_retries=3, base_wait_seconds=15, proxy_url=LETTERBOXD_PROXY_URL, proxy_attempted=False):
    """
    Makes a request with appropriate rate limiting and basic retry logic for 429 errors.
    """
    tried_curl = False
    for attempt in range(max_retries):
        rate_limit(is_ajax_call=is_ajax_call) # Pass the flag here
        try:
            proxies = _build_proxy_dict(proxy_url) if proxy_attempted else None
            response = session.get(url, timeout=20, proxies=proxies) # Increased timeout

            # Detect Cloudflare challenge pages that may still return HTML instead of JSON/real content.
            if response.status_code == 200 and isinstance(response.text, str):
                body_snippet = response.text[:1200].lower()
                if 'just a moment' in body_snippet and 'cloudflare' in body_snippet:
                    logging.warning(f"CHALLENGE_DETECTED: Cloudflare challenge returned for {url}")
                    raise LetterboxdScrapeBlocked(f"Cloudflare challenge page detected for {url}")

            if response.status_code == 403:
                logging.warning(f"REQUEST_FORBIDDEN: Received 403 for {url}")
                if proxy_url and not proxy_attempted:
                    logging.info(f"PROXY_FALLBACK: Retrying {url} via proxy {proxy_url}")
                    return make_request_with_basic_retry(
                        url,
                        session,
                        is_ajax_call=is_ajax_call,
                        max_retries=max_retries,
                        base_wait_seconds=base_wait_seconds,
                        proxy_url=proxy_url,
                        proxy_attempted=True,
                    )
                raise LetterboxdScrapeBlocked(f"Letterboxd returned 403 for {url}")

            if response.status_code == 429:
                # For 429, the wait time should be significant, especially for AJAX
                wait_time_multiplier = 2 if is_ajax_call else 1
                wait_time = (base_wait_seconds * (attempt + 1)) * wait_time_multiplier
                
                logging.warning(f"Received 429 for {url}. Retrying in {wait_time}s (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                
                # After a 429-induced sleep, reset the specific timer so next call isn't immediately delayed by *our* rate_limit
                if is_ajax_call:
                    global last_request_time_ajax
                    last_request_time_ajax = time.time()
                else:
                    global last_request_time_normal
                    last_request_time_normal = time.time()
                continue 

            response.raise_for_status() 
            return response
        except LetterboxdScrapeBlocked:
            raise
        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed for {url} (Attempt {attempt + 1}/{max_retries}): {e}")

            # Try curl_cffi as a fallback once if available — it provides a more realistic TLS/HTTP2 fingerprint
            if curl_requests and not tried_curl:
                tried_curl = True
                try:
                    logging.info(f"FALLBACK: Trying curl_cffi for {url}")
                    proxies = _build_proxy_dict(proxy_url) if proxy_attempted else None
                    curl_resp = curl_requests.get(url, timeout=20, proxies=proxies, headers=session.headers)
                    if curl_resp.status_code == 200:
                        logging.info(f"FALLBACK: curl_cffi request succeeded for {url}")
                        return curl_resp
                    else:
                        logging.warning(f"FALLBACK: curl_cffi returned {curl_resp.status_code} for {url}")
                except Exception as e_curl:
                    logging.warning(f"FALLBACK: curl_cffi request failed for {url}: {e_curl}")

            if attempt == max_retries - 1:
                raise 
            # Shorter sleep for general network issues, but still with some backoff
            time.sleep((base_wait_seconds / 2) * (attempt + 1)) 
    
    logging.error(f"All retries failed for {url}")
    raise requests.exceptions.RequestException(f"All retries failed for {url}")


def get_actual_poster_url(film_slug, session, film_id=None):
    """
    Get the actual poster URL by constructing it from the film ID and slug.
    The pattern is: https://a.ltrbxd.com/resized/film-poster/{digits}/{film_id}-{slug}-0-150-0-225-crop.jpg
    """
    if not film_id:
        # If no film_id provided, try to get it from the film page
        film_url = f"https://letterboxd.com/film/{film_slug}/"
        try:
            response = make_request_with_basic_retry(film_url, session, is_ajax_call=False)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for film ID in data attributes
            film_id_elem = soup.find(attrs={'data-film-id': True})
            if film_id_elem:
                film_id = film_id_elem.get('data-film-id')
            else:
                logging.warning(f"POSTER_URL: No film ID found for {film_slug}")
                return EMPTY_POSTER_URL
                
        except Exception as e:
            logging.error(f"POSTER_URL: Error getting film ID for {film_slug}: {e}")
            return EMPTY_POSTER_URL
    
    try:
        # Construct poster URL using the correct pattern
        film_id_str = str(film_id)
        
        # Split film ID into individual digits for the path (don't pad with zeros)
        path_parts = '/'.join(film_id_str)
        
        # Try different slug variations for the poster URL
        slug_variations = [
            film_slug,  # Original slug (e.g., "burning-2018")
            film_slug.split('-')[0] if '-' in film_slug else film_slug,  # First part only (e.g., "burning")
        ]
        
        for slug_variant in slug_variations:
            poster_url = f"https://a.ltrbxd.com/resized/film-poster/{path_parts}/{film_id}-{slug_variant}-0-150-0-225-crop.jpg"
            
            # Test if the URL works. Set Referer to the film page for a more realistic request.
            old_referer = session.headers.get('Referer')
            session.headers['Referer'] = f'https://letterboxd.com/film/{film_slug}/'
            try:
                test_response = session.head(poster_url, timeout=5)
            finally:
                # Restore original referer
                if old_referer is not None:
                    session.headers['Referer'] = old_referer
                else:
                    session.headers.pop('Referer', None)
            if test_response.status_code == 200:
                logging.info(f"POSTER_URL: Found actual poster for {film_slug} using slug '{slug_variant}': {poster_url}")
                return poster_url
            else:
                logging.debug(f"POSTER_URL: Slug '{slug_variant}' returned {test_response.status_code} for {film_slug}")
        
        logging.warning(f"POSTER_URL: No working poster URL found for {film_slug}")
        return EMPTY_POSTER_URL
            
    except Exception as e:
        logging.error(f"POSTER_URL: Error constructing poster URL for {film_slug}: {e}")
        return EMPTY_POSTER_URL


def scrape_letterboxd_favorites(username, session):
    """
    Scrapes favorite movies from the main profile page.
    Updated to work with the new Letterboxd structure using React components.
    No longer needs AJAX calls as all data is available in the main page.
    """
    profile_url = f'https://letterboxd.com/{username}/'
    logging.info(f"FAVORITES_DIRECT: Scraping profile for {username}: {profile_url}")
    favorite_movies_details = []

    try:
        # Get the main profile page
        profile_response = make_request_with_basic_retry(profile_url, session, is_ajax_call=False)
        profile_soup = BeautifulSoup(profile_response.content, 'html.parser')

        # Find the favorites section with the current Letterboxd markup.
        favorites_section = profile_soup.select_one('section#favourites')
        if not favorites_section:
            favorites_section_heading = profile_soup.find('h2', class_='section-heading', string=lambda t: t and 'Favorite' in t.strip())
            if favorites_section_heading:
                favorites_section = favorites_section_heading.find_parent('section')

        if not favorites_section:
            logging.warning(f"FAVORITES_DIRECT: No 'Favorite Films' section found for {username} on main page.")
            return []

        favorite_film_items = favorites_section.select("div.react-component[data-component-class*='LazyPoster'][data-item-slug]")

        if not favorite_film_items:
            logging.warning(f"FAVORITES_DIRECT: No favorite film items found for {username} in favorites section.")
            return []
        
        logging.info(f"FAVORITES_DIRECT: Found {len(favorite_film_items)} favorite films for {username}.")

        # Extract data from each favorite film
        for lazy_poster_div in favorite_film_items:
            film_slug = lazy_poster_div.get('data-item-slug')
            film_name = lazy_poster_div.get('data-item-name', 'Unknown Film')
            film_id = lazy_poster_div.get('data-film-id')
            poster_path = lazy_poster_div.get('data-poster-url')

            # Prefer the poster URL returned directly in the profile markup.
            if poster_path and poster_path.startswith('/'):
                poster_url = f"https://letterboxd.com{poster_path}"
            else:
                poster_url = get_actual_poster_url(film_slug, session, film_id)

            current_movie_details = {
                'code': film_slug,
                'name': film_name,
                'url': poster_url
            }
            
            favorite_movies_details.append(current_movie_details)
            logging.info(f"FAVORITES_DIRECT: Extracted {film_slug}: {film_name} -> {poster_url}")
            
        logging.info(f"FAVORITES_DIRECT: FINAL - Scraped {len(favorite_movies_details)} favorites for {username}. Data: {favorite_movies_details}")
        return favorite_movies_details

    except LetterboxdScrapeBlocked:
        raise
    except requests.exceptions.RequestException as e_profile:
        logging.error(f"FAVORITES_DIRECT: All retries failed for main profile for {username}: {e_profile}", exc_info=True)
        return []
    except Exception as e_general:
        logging.error(f"FAVORITES_DIRECT: An unexpected error occurred for {username}: {e_general}", exc_info=True)
        return []


def scrape_letterboxd_fans(favorite_movie_slugs, session): # Changed arg to be more specific
    """
    Scrapes usernames, names, and profile picture links of Letterboxd users
    who are fans of the given list of movies. Uses NORMAL rate limit.
    IMPORTANT: This function uses /s/search/ which is disallowed by robots.txt.
    """
    if not favorite_movie_slugs:
        logging.warning("FANS_SEARCH: Called with no favorite movie slugs, returning empty.")
        return [], False
        
    search_term = '+'.join(f'fan:{slug}' for slug in favorite_movie_slugs)
    search_url = f"https://letterboxd.com/s/search/{search_term}/"

    logging.info(f"FANS_SEARCH: Scraping fans for movies: {favorite_movie_slugs}")
    logging.debug(f"FANS_SEARCH: Search URL: {search_url}")

    try:
        response = make_request_with_basic_retry(search_url, session, is_ajax_call=False) # NORMAL call
        soup = BeautifulSoup(response.content, 'html.parser')
        fan_results_li = soup.find_all('li', class_='search-result -person')

        if not fan_results_li:
            logging.warning(f"FANS_SEARCH: No fans found for movies: {favorite_movie_slugs}")
            return [], False

        fan_data_list = []
        for result_li in fan_results_li:
                username_link_a = result_li.select_one('a.name')
                if not username_link_a:
                    continue

                name = username_link_a.text.strip()
                for tag in ("Pro", "Patron", "Crew"):
                    if name.endswith(f" {tag}"):
                        name = name[: -len(tag) - 1].strip()

                summary_div = result_li.select_one('div.person-summary.-search')
                username = "unknown_user"
                if summary_div:
                    username_small_element = summary_div.select_one('small.metadata')
                    if username_small_element:
                        username = username_small_element.text.strip()

                picture_img_tag = result_li.select_one('img')
                picture_link_url = None
                if picture_img_tag:
                    picture_link_url = picture_img_tag.get('data-src') or picture_img_tag.get('src')

                fan_data_list.append({
                    'username': username,
                    'name': name,
                    'picture_link': picture_link_url
                })

        more_results_available_flag = len(fan_data_list) == 20 # Heuristic for pagination
        logging.info(f"FANS_SEARCH: Scraped {len(fan_data_list)} fans for movies: {favorite_movie_slugs}")
        return fan_data_list, more_results_available_flag
    except LetterboxdScrapeBlocked:
        raise
    except requests.exceptions.RequestException as e:
        logging.error(f"FANS_SEARCH: All retries failed for search: {search_term} - {e}")
        return [], False
    except Exception as e_general_fans:
        logging.error(f"FANS_SEARCH: An unexpected error occurred while scraping fans for {search_term}: {e_general_fans}", exc_info=True)
        return [], False


def get_fans_for_combinations(list_of_favorite_movie_dicts, username_to_exclude, session):
    """
    Gets fans for all possible combinations of favorite movies, from 4 down to 2.
    Excludes the searching user (case-insensitive) and only includes fans in the largest combination they appear in.
    """
    fans_by_combo_size_dict = {}
    seen_fan_usernames_set = set() # To ensure a fan appears only in their largest matching combo
    scraping_was_blocked = False # Flag to track if we hit any 403 errors
    
    # Ensure we have a list of slugs from the favorite movie dicts
    favorite_movie_slugs = [fav_dict['code'] for fav_dict in list_of_favorite_movie_dicts if isinstance(fav_dict, dict) and 'code' in fav_dict]

    if not favorite_movie_slugs:
        logging.warning("COMBINATIONS: No favorite movie slugs to make combinations from.")
        return {}, False

    # Iterate from largest combinations (4) down to smallest (2)
    for combination_length in range(min(len(favorite_movie_slugs), 4), 1, -1): # Max 4, min 2
        fans_by_combo_size_dict[combination_length] = []
        
        for current_movie_slug_combination in itertools.combinations(favorite_movie_slugs, combination_length):
            try:
                # scrape_letterboxd_fans uses NORMAL rate limit
                fans_for_this_slug_combo, more_results_flag = scrape_letterboxd_fans(list(current_movie_slug_combination), session)
                
                if fans_for_this_slug_combo:
                    unique_new_fans_for_this_combo = []
                    for fan_dict in fans_for_this_slug_combo:
                        fan_username = fan_dict.get('username', '').lower()
                        if fan_username != username_to_exclude.lower() and fan_username not in seen_fan_usernames_set:
                            unique_new_fans_for_this_combo.append(fan_dict)
                            seen_fan_usernames_set.add(fan_username) # Add to global seen set

                    if unique_new_fans_for_this_combo: # Only add if there are new, unique fans
                        fans_by_combo_size_dict[combination_length].append({
                            "movies": list(current_movie_slug_combination), 
                            "fans": unique_new_fans_for_this_combo,
                            "more_results": more_results_flag
                        })
            except LetterboxdScrapeBlocked as e:
                scraping_was_blocked = True
                logging.error(f"COMBINATIONS: Scraping blocked for combo {current_movie_slug_combination}: {e}")
                continue
            except requests.exceptions.RequestException as e:
                logging.error(f"COMBINATIONS: Failed to get fans for combo {current_movie_slug_combination}: {e}")
                continue # Move to the next combination
    return fans_by_combo_size_dict, scraping_was_blocked


def get_all_fans_of_favorites(username):
    """
    Main orchestrator function.
    Gets all fans of a user's favorites, including combinations.
    Returns a dictionary with the fans, and a separate list of favorite movies.
    """
    logging.info(f"GET_ALL_FANS: Starting process for user: {username}")

    # Create and prime a session for this task to handle cookies and appear more like a real browser.
    session = create_letterboxd_session()
    try:
        logging.info("SESSION: Priming session with a visit to the homepage to get cookies.")
        # Use the resilient request wrapper so retries, rate-limiting and fallbacks apply
        make_request_with_basic_retry("https://letterboxd.com/", session, is_ajax_call=False)
        # Persist cookies for reuse across runs (cf_clearance)
        save_session_cookies(session)
    except requests.exceptions.RequestException as e:
        logging.error(f"SESSION: Failed to prime session, scraping may fail: {e}")
        # We can continue, but it's a bad sign.

    try:
        list_of_favorite_movie_dicts = scrape_letterboxd_favorites(username, session)
    except LetterboxdScrapeBlocked as e:
        logging.error(f"GET_ALL_FANS: Scraping blocked for {username}: {e}")
        return {'fans': {}, 'movies': [], 'scraping_error': True, 'error': str(e)}
    except Exception as e:
        logging.error(f"GET_ALL_FANS: Unexpected error scraping favorites for {username}: {e}", exc_info=True)
        return {'fans': {}, 'movies': [], 'scraping_error': True, 'error': 'Favorite scraping failed.'}

    if not list_of_favorite_movie_dicts:
        logging.warning(f"GET_ALL_FANS: No favorites found for {username}, cannot find fan combinations.")
        return {'fans': {}, 'movies': [], 'scraping_error': False}

    # get_fans_for_combinations calls scrape_letterboxd_fans (NORMAL rate limit)
    fans_by_combination_dict, scraping_was_blocked = get_fans_for_combinations(list_of_favorite_movie_dicts, username, session) # Pass username to exclude
    
    result = {
        'fans': fans_by_combination_dict,
        'movies': list_of_favorite_movie_dicts, # Ensure it's a list
        'scraping_error': scraping_was_blocked
    }

    if scraping_was_blocked:
        result['warning'] = 'Letterboxd blocked or limited one or more fan searches. Results may be incomplete.'

    logging.info(f"GET_ALL_FANS: Finished process for user: {username}")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2: # Corrected from your original if len(sys.argv) != 2:
        print("Usage: python tasks.py <username>")
        sys.exit(1)

    username_to_test = sys.argv[1]
    print(f"--- TESTING get_all_fans_of_favorites for: {username_to_test} ---")
    
    results_dict = get_all_fans_of_favorites(username_to_test)

    print(f"\n--- Favorite films for {username_to_test}: ---")
    if results_dict and results_dict.get('movies'):
        for film_dict in results_dict['movies']:
            print(f"  - Name: {film_dict.get('name')}, Code: {film_dict.get('code')}, Poster URL: {film_dict.get('url')}")
    else:
        print("  No favorite movies found or parsed.")

    print(f"\n--- Fans for combinations for {username_to_test}: ---")
    if results_dict and results_dict.get('fans') and any(results_dict['fans'].values()):
        for combo_size, combinations_list in results_dict['fans'].items():
            if combinations_list: # If there are any combinations of this size that had fans
                print(f"\n  Fans of {combo_size} common movies:")
                for combo_data_dict in combinations_list:
                    print(f"    Movies Combination: {', '.join(combo_data_dict.get('movies', []))}")
                    if combo_data_dict.get('fans'):
                        for fan_dict_item in combo_data_dict['fans']:
                            print(f"      - Fan: {fan_dict_item.get('name')} (@{fan_dict_item.get('username')}), Pic: {fan_dict_item.get('picture_link')}")
                    else:
                        print("      No unique new fans found for this specific combination.")
                    if combo_data_dict.get('more_results'):
                        print("      (More fan results might be available on Letterboxd for this combination)")
    else:
        print("  No fan combination data found (or all combinations resulted in no new/unique fans).")

    # Example of direct call to scrape_letterboxd_favorites (for focused testing)
    # print(f"\n--- Direct test of scrape_letterboxd_favorites for: {username_to_test} ---")
    # direct_favorites = scrape_letterboxd_favorites(username_to_test)
    # if direct_favorites:
    #     for film in direct_favorites:
    #         print(f"  - {film.get('name')} ({film.get('code')}) - Poster: {film.get('url')}")
    # else:
    #     print("  No favorites found directly.")