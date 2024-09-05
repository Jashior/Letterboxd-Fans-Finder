import requests
from bs4 import BeautifulSoup
import logging
import time
import itertools

# Set up basic logging
logging.basicConfig(level=logging.INFO)

# Rate limiting variables
REQUESTS_PER_MINUTE = 60
SECONDS_PER_MINUTE = 60
last_request_time = 0

# Rate limiting function
def rate_limit():
    global last_request_time
    current_time = time.time()
    time_since_last_request = current_time - last_request_time
    if time_since_last_request < SECONDS_PER_MINUTE / REQUESTS_PER_MINUTE:
        sleep_time = SECONDS_PER_MINUTE / REQUESTS_PER_MINUTE - time_since_last_request
        logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f} seconds")
        time.sleep(sleep_time)
    last_request_time = time.time()

def scrape_letterboxd_favorites(username):
    """
    Scrapes favorite movies from a Letterboxd user's profile, including movie slugs, 
    names, and poster URLs.

    Args:
        username (str): The Letterboxd username to scrape.

    Returns:
        list: A list of dictionaries, each containing:
            - 'code': The movie slug (e.g., "burning-2018")
            - 'name': The movie title (e.g., "Burning (2018)")
            - 'url': The URL of the movie poster
    """
    rate_limit()
    url = f'https://letterboxd.com/{username}'
    logging.info(f"Scraping favorites for user: {username}")

    try:
        response = requests.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        favorites_section = soup.find('h2', class_='section-heading', text=lambda t: 'Favorite films' in t)
        if not favorites_section:
            logging.warning(f"No 'Favorite films' section found for {username}")
            return []

        poster_list = favorites_section.find_next('ul', class_='poster-list')
        if not poster_list:
            logging.warning(f"No poster list found in the favorites section for {username}")
            return []

        favorite_movies = []
        for list_item in poster_list.find_all('li'):
            film_poster_div = list_item.find('div', class_='film-poster')
            if film_poster_div:
                film_slug = film_poster_div.get('data-film-slug')
                if film_slug:
                    # Make a separate request for the movie data:
                    movie_data_url = f"https://letterboxd.com/ajax/poster/film/{film_slug}/std/150x225/"
                    movie_data_response = requests.get(movie_data_url)
                    movie_data_response.raise_for_status()
                    movie_data_soup = BeautifulSoup(movie_data_response.content, 'html.parser')
                    # Extract the details:
                    poster_img = movie_data_soup.find('img', class_='image')
                    if poster_img:
                        poster_url = poster_img['src']
                    film_name = movie_data_soup.find('span', class_='frame-title').text.strip()
                    favorite_movies.append({
                        'code': film_slug,
                        'name': film_name,
                        'url': poster_url
                    })

        logging.info(f"Scraped {len(favorite_movies)} favorites for {username}")
        return favorite_movies

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Letterboxd profile for {username}: {e}")
        return []
    
def scrape_letterboxd_fans(favorites):
    """
    Scrapes usernames, names, and profile picture links of Letterboxd users 
    who are fans of the given list of movies.

    Args:
        favorites (list): A list of movie slugs (e.g., ["burning-2018", "the-conversation"]).

    Returns:
        tuple: A tuple containing:
            - list: A list of dictionaries, each containing user data.
            - bool: True if more results are available (pagination exists), False otherwise.
    """
    rate_limit()
    search_term = '+'.join(f'fan:{favorite}' for favorite in favorites)
    search_url = f"https://letterboxd.com/s/search/{search_term}/"

    logging.info(f"Scraping fans for movies: {favorites}")
    logging.debug(f"Search URL: {search_url}")

    try:
        response = requests.get(search_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        fan_results = soup.find_all('li', class_='search-result -person')

        if not fan_results:
            logging.warning(f"No fans found for movies: {favorites}")
            logging.debug(f"Search results HTML: {soup.prettify()}")
            return [], False  # Return an empty list and False

        fan_data = []
        for result in fan_results:
            username_link = result.find('a', class_='name')
            if username_link:
                name = username_link.find_parent('h3').text.strip()
                for tag in ("Pro", "Patron", "Crew"):
                    name = name.replace(f" {tag}", "") 

                # Get the username from the small element
                username_element = result.find('div', class_='person-summary -search').find('small', class_='metadata')
                username = username_element.text.strip()

                picture_link = result.find('img')['src']
                fan_data.append({
                    'username': username,
                    'name': name,
                    'picture_link': picture_link
                })

        # Assume more results are available if 20 results are returned
        more_results_available = len(fan_data) == 20

        logging.info(f"Scraped {len(fan_data)} fans for movies: {favorites}")
        return fan_data, more_results_available  # Return the data and the flag

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Letterboxd search results for: {search_term} - {e}")
        return [], False  # Return an empty list and False 

def get_fans_for_combinations(favorites, username):
    """
    Gets fans for all possible combinations of favorite movies, from 4 down to 2.
    Excludes the searching user (case-insensitive) and only includes fans in the largest combination they appear in.

    Args:
        favorites (list): A list of movie dictionaries.
        username (str): The Letterboxd username of the user performing the search.

    Returns:
        dict: A dictionary where:
            - Keys are the combination sizes (4, 3, 2)
            - Values are lists of dictionaries, each representing a combination:
                - 'movies': The list of movie slugs in the combination
                - 'fans': A list of fan dictionaries
                - 'more_results': True if there are more results for that combination, False otherwise.
    """
    fan_data = {}
    seen_usernames = set()  # Track seen usernames across all combinations
    for combination_size in range(4, 1, -1):
        fan_data[combination_size] = []  # Initialize an empty list for each combination size
        for combination in itertools.combinations(favorites, combination_size):
            # Extract movie slugs from the dictionaries:
            combination_slugs = [movie['code'] for movie in combination]
            fans_for_combination, more_results = scrape_letterboxd_fans(combination_slugs)
            if fans_for_combination:
                # Filter out the searching user (case-insensitive) and keep only unique fans
                unique_fans = []
                for fan in fans_for_combination:
                    # Case-insensitive comparison:
                    if fan['username'].lower() != username.lower() and fan['username'] not in seen_usernames:
                        unique_fans.append(fan)
                        seen_usernames.add(fan['username'])
                fan_data[combination_size].append({
                    "movies": combination_slugs,
                    "fans": unique_fans,
                    "more_results": more_results
                })
    return fan_data

def scrape_fans_of_favorites_combinations(username):
    """
    Scrapes fans for all combinations of a user's favorites (4 down to 2).

    Args:
        username (str): The Letterboxd username.

    Returns:
        dict: A dictionary with keys representing the number of movies in a combination 
             and values being lists of fan dictionaries, including the 'combination' key.
    """
    favorites = scrape_letterboxd_favorites(username)
    return get_fans_for_combinations(favorites, username)  # Pass username here

def get_all_fans_of_favorites(username):
    """
    Gets all fans of a user's favorites, including combinations.
    Returns a dictionary with the fans, and a separate list of favorite movies.

    Args:
        username (str): The Letterboxd username.

    Returns:
        dict: A dictionary containing:
            - 'fans': A dictionary of fans grouped by combination size
            - 'movies': A list of dictionaries, each representing a favorite movie
    """
    fans_by_combination = scrape_fans_of_favorites_combinations(username)
    favorite_movies = scrape_letterboxd_favorites(username)
    return {
        'fans': fans_by_combination,
        'movies': favorite_movies
    }
    
    
# You can keep this function in the same tasks.py file
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python tasks.py <username>")
        sys.exit(1)

    username = sys.argv[1]

    favorites = scrape_letterboxd_favorites(username)
    print(f"Favorite films for {username}:")
    for film in favorites:
        print(film)

    # Example usage of the new function
    example_favorites = ["burning-2018", "the-conversation", "under-the-skin-2013"]
    fans = scrape_letterboxd_fans(example_favorites)
    print(f"\nFans of {example_favorites}:")
    for fan in fans:
        print(fan)