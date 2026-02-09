import requests
from bs4 import BeautifulSoup
import logging
import time
import itertools
import sys

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - TASKS.PY - %(message)s')

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

    if time_since_last_request < required_delay:
        sleep_time = required_delay - time_since_last_request
        logging.info(f"{log_prefix}: Sleeping for {sleep_time:.2f} seconds")
        time.sleep(sleep_time)
    
    if is_ajax_call:
        last_request_time_ajax = time.time()
    else:
        last_request_time_normal = time.time()


def make_request_with_basic_retry(url, session, is_ajax_call=False, max_retries=3, base_wait_seconds=15):
    """
    Makes a request with appropriate rate limiting and basic retry logic for 429 errors.
    """
    for attempt in range(max_retries):
        rate_limit(is_ajax_call=is_ajax_call) # Pass the flag here
        try:
            response = session.get(url, timeout=20) # Increased timeout
            
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
        except requests.exceptions.RequestException as e:
            # If we get a 403, it's a permanent block. Don't retry.
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 403:
                logging.error(f"Request forbidden for {url} (403). Aborting. This is likely due to scraping a disallowed endpoint.")
                raise # Re-raise immediately to fail the task.

            logging.error(f"Request failed for {url} (Attempt {attempt + 1}/{max_retries}): {e}")
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
            
            # Test if the URL works
            test_response = session.head(poster_url, timeout=5)
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

        # Find the favorites section
        poster_list_ul = None
        favorites_section_heading = profile_soup.find('h2', class_='section-heading', string=lambda t: t and 'Favorite' in t.strip())
        if favorites_section_heading:
            poster_list_ul = favorites_section_heading.find_next_sibling('ul', class_='poster-list')
        if not poster_list_ul: # Fallback selector
            poster_list_ul = profile_soup.select_one("section#favourites ul.poster-list")

        if not poster_list_ul:
            logging.warning(f"FAVORITES_DIRECT: No 'Favorite Films' poster list found for {username} on main page.")
            return []

        # Find all favorite film items - updated for new structure
        favorite_film_items = []
        for film_li in poster_list_ul.find_all('li', class_='posteritem', recursive=False):
            # Look for the React component with LazyPoster
            lazy_poster_div = film_li.find('div', class_='react-component', attrs={'data-component-class': lambda x: x and 'LazyPoster' in x})
            if lazy_poster_div and lazy_poster_div.get('data-item-slug'):
                favorite_film_items.append(lazy_poster_div)
        
        logging.info(f"FAVORITES_DIRECT: Found {len(favorite_film_items)} favorite films for {username}.")

        # Extract data from each favorite film
        for lazy_poster_div in favorite_film_items:
            film_slug = lazy_poster_div.get('data-item-slug')
            film_name = lazy_poster_div.get('data-item-name', 'Unknown Film')
            film_id = lazy_poster_div.get('data-film-id')
            
            # Get actual poster URL using the film ID and slug
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

    except requests.exceptions.RequestException as e_profile:
        if hasattr(e_profile, 'response') and e_profile.response is not None and e_profile.response.status_code == 403:
            raise e_profile
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
            username_link_a = result_li.find('a', class_='name')
            if username_link_a:
                name_h3 = username_link_a.find_parent('h3')
                name = "Unknown Name"
                if name_h3:
                    name = name_h3.text.strip()
                    for tag in ("Pro", "Patron", "Crew"): 
                        name = name.replace(f" {tag}", "")

                username_small_element = result_li.find('div', class_='person-summary -search').find('small', class_='metadata')
                username = "unknown_user" 
                if username_small_element:
                    username = username_small_element.text.strip()

                picture_img_tag = result_li.find('img')
                picture_link_url = None 
                if picture_img_tag:
                    picture_link_url = picture_img_tag.get('src') # Use .get() to avoid KeyError if 'src' is missing
                
                fan_data_list.append({
                    'username': username,
                    'name': name,
                    'picture_link': picture_link_url
                })

        more_results_available_flag = len(fan_data_list) == 20 # Heuristic for pagination

        logging.info(f"FANS_SEARCH: Scraped {len(fan_data_list)} fans for movies: {favorite_movie_slugs}")
        return fan_data_list, more_results_available_flag

    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response is not None and e.response.status_code == 403:
            raise e
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
            except requests.exceptions.RequestException as e:
                # If a specific combination fails (e.g., 403), log it and continue to the next.
                if hasattr(e, 'response') and e.response is not None and e.response.status_code == 403:
                    scraping_was_blocked = True
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

    # Create and prime a session for this task to handle cookies and appear more like a real browser
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })
    try:
        logging.info("SESSION: Priming session with a visit to the homepage to get cookies.")
        session.get("https://letterboxd.com/", timeout=15)
    except requests.exceptions.RequestException as e:
        logging.error(f"SESSION: Failed to prime session, scraping may fail: {e}")
        # We can continue, but it's a bad sign.

    list_of_favorite_movie_dicts = scrape_letterboxd_favorites(username, session)
    
    if not list_of_favorite_movie_dicts:
        logging.warning(f"GET_ALL_FANS: No favorites found for {username}, cannot find fan combinations.")
        return {'fans': {}, 'movies': [], 'scraping_error': False} 

    # get_fans_for_combinations calls scrape_letterboxd_fans (NORMAL rate limit)
    fans_by_combination_dict, scraping_was_blocked = get_fans_for_combinations(list_of_favorite_movie_dicts, username, session) # Pass username to exclude
    
    logging.info(f"GET_ALL_FANS: Finished process for user: {username}")
    return {
        'fans': fans_by_combination_dict,
        'movies': list_of_favorite_movie_dicts, # Ensure it's a list
        'scraping_error': scraping_was_blocked
    }


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