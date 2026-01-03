class City:
    def __init__(self, line: str):
        data = line.strip().split(',')
        if len(data) != 5:
            self.enable = False
        else:
            self.enable = True
            self.prefecture = data[0]
            self.pronounce_prefecture = data[1]
            self.city = data[2]
            self.pronounce_city = data[3]
            self.url = data[4].strip()
    
    def __str__(self):
        return f'prefecture: {self.prefecture}, pronounce_prefecture: {self.pronounce_prefecture}, city: {self.city}, pronounce_city: {self.pronounce_city}, url: {self.url}'

class CityManager:
    def __init__(self, path='localgovlistjp/localgov_utf8_lf.csv'):
        self.data_list = [City(d) for d in open(path, 'r', encoding='utf-8').readlines()]
        self.data_list = [d for d in self.data_list if d.enable]
        self.map = {d.url:d for d in self.data_list}
