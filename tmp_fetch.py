import urllib.request

url = 'https://docs.google.com/spreadsheets/d/1JGmjup7TtkDPibsn6A9WzWmvHF0myMDIMlGTGSdiAqM/htmlview'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as f:
    html = f.read().decode('utf-8')
    with open('docs.html', 'w', encoding='utf-8') as out:
        out.write(html)
print('Done writing docs.html')
