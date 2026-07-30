import requests
from bs4 import BeautifulSoup

def obtener_proxies_gratuitos():
    url = "https://free-proxy-list.net/"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    proxies = []
    for row in table.find_all('tr')[1:]:  # Saltar cabecera
        cols = row.find_all('td')
        if cols:
            ip = cols[0].text.strip()
            port = cols[1].text.strip()
            proxies.append(f"http://{ip}:{port}")
    return proxies

if __name__ == "__main__":
    proxies = obtener_proxies_gratuitos()
    with open('proxies.txt', 'w') as f:
        f.write('\n'.join(proxies))
    print(f"✅ {len(proxies)} proxies guardados en proxies.txt")
