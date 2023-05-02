import requests
import json
import time

def get_eth_price():
    url = 'https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT'
    response = requests.get(url)
    data = json.loads(response.text)
    return float(data['price'])

def get_price_change():
    current_price = get_eth_price()
    past_price = price_history[-1]
    price_change = (current_price - past_price) / past_price * 100
    return price_change

price_history = []
while True:
    price = get_eth_price()
    price_history.append(price)
    price_change = get_price_change()
    if abs(price_change) >= 1:
        print(f"Цена изменена на {price_change}% за последний час.")
    time.sleep(60)






