"""Cheap checks for obvious language drift; no extra AI translation request."""
import re


def language_instruction(lang):
    return ('Pisz całą narrację, opcje i uzasadnienia wyłącznie po polsku, naturalnym językiem. '
            'Angielskie pozostają tylko klucze JSON i identyfikatory zasobów. Nie kopiuj języka danych wejściowych. '
            if lang == 'pl' else 'Write all narrative, choices and reasons in English. ')


def wrong_language(text, lang):
    if lang != 'pl':return False
    words=re.findall(r'[^\W\d_]+',text.casefold())
    english=set('the and with from your their this that these those should would could will '
                'is are was were have has into through before after without while to of '
                'send choose gather negotiate investigate protect repair allocate accept reject '
                'scouts supplies council merchants village kingdom cautiously balanced decisive '
                'provide reinforce dispatch offer establish inspect secure distribute sell buy '
                'seek aid help explore refuse wait support abandon respond'.split())
    polish=set('i w na z do od o po za dla przez przy oraz się nie to jest są był była '
               'będzie zostanie które który ich jego jej aby lub ale gdy jeśli wyślij wybierz '
               'zbierz uzgodnij negocjuj zabezpiecz napraw kupcy rada państwo dostawy'.split())
    en=sum(word in english for word in words)
    pl=sum(word in polish or bool(re.search('[ąćęłńóśźż]',word)) for word in words)
    return en>=2 and en>pl


def validate_language(text, lang):
    if wrong_language(text,lang):raise ValueError('Wrong narrative language')
