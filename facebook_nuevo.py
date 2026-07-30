```python
import requests
from bs4 import BeautifulSoup
import json
import re

def load_config(file_path):
    with open(file_path, 'r') as file:
        config = json.load(file)
    return config['countries'], config['subgenres'], config['types'], confi[5D[K
config['cities']

def get_groups(country, subgenre, genre_type, city):
    # Replace this URL with the actual Facebook search URL for groups
    url = f"https://www.facebook.com/search/groups/?q={country}+{subgenre}+[65D[K
f"https://www.facebook.com/search/groups/?q={country}+{subgenre}+{genre_typf"https://www.facebook.com/search/groups/?q={country}+{subgenre}+genre_type}+{city}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKi[10D[K
AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    group_links = [a['href'] for a in soup.find_all('a', href=True) if '/gr[4D[K
'/groups/' in a['href']]
    return group_links

def extract_organizer(group_link):
    url = f"https://www.facebook.com{group_link}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKi[10D[K
AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Using regex to extract the organizer name
    organizer_pattern = re.compile(r'data-testid="event-card-host" content=[8D[K
content="(.*?)"')
    match = organizer_pattern.search(str(soup))
    
    if match:
        organizer = match.group(1)
    else:
        organizer = "Organizer not found"
    
    return organizer

def main():
    config_path = 'config_temp.json'
    countries, subgenres, types, cities = load_config(config_path)

    for country in countries:
        for subgenre in subgenres:
            for genre_type in types:
                for city in cities:
                    group_links = get_groups(country, subgenre, genre_type,[11D[K
genre_type, city)
                    for group_link in group_links:
                        organizer = extract_organizer(group_link)
                        print(f"Group: {group_link}, Organizer: {organizer}[11D[K
{organizer}")

if __name__ == "__main__":
    main()
```

Este código realiza el scraping de grupos de Facebook utilizando combinacio[10D[K
combinaciones de países, subgéneros, tipos y ciudades definidas en `config_[8D[K
`config_temp.json`. Extrae el nombre del organizador de los grupos usando u[1D[K
una expresión regular.

