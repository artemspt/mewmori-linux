"""FSRS-6 — уравнение, по которому решается, когда показать слово снова.

Здесь нет ни хранения, ни ввода-вывода: на входе состояние карточки и оценка,
на выходе новое состояние. `cards.py` — это колода, а это то, что её двигает.

## Почему не Лейтнер и не SM-2

Лейтнер — список интервалов, одинаковый для всех слов. SM-2 (1987) добавляет к
нему коэффициент лёгкости у каждой карточки, и это уже лучше, но обе формулы
придуманы из головы: их числа никто не подбирал по тому, как люди на самом деле
забывают.

FSRS подобран. Это модель DSR — **трудность, стабильность, вспоминаемость** —
с двадцатью одним весом, обученным на сотнях миллионов настоящих повторов:

- **S, стабильность** — за сколько дней вероятность вспомнить падает до 90%.
  Это и есть интервал при желаемом удержании 0.9.
- **D, трудность** — 1..10, насколько это слово тяжело лично тебе.
- **R, вспоминаемость** — вероятность вспомнить прямо сейчас, по кривой
  забывания от S и прошедших дней.

Главная разница со всем, что было до: **новая стабильность зависит от R в
момент ответа**. Слово, которое ты вспомнил, когда почти забыл, укрепляется
куда сильнее, чем то же слово, повторённое на следующий день, — и формула это
знает. Ни Лейтнер, ни SM-2 не смотрят, *когда* ты ответил, только *как*.

    R(t,S) = (1 + FACTOR · t/S) ^ DECAY          DECAY = −w₂₀, FACTOR = 0.9^(1/DECAY) − 1
    интервал(S) = S/FACTOR · (удержание^(1/DECAY) − 1)
    S₀(оценка) = w[оценка−1]
    D₀(оценка) = w₄ − e^(w₅·(оценка−1)) + 1
    вспомнил:   S′ = S·(1 + e^w₈·(11−D)·S^−w₉·(e^((1−R)·w₁₀) − 1)·штраф·бонус)
    забыл:      S′ = min(w₁₁·D^−w₁₂·((S+1)^w₁₃ − 1)·e^((1−R)·w₁₄),  S/e^(w₁₇·w₁₈))

Веса и формулы сверены с исходником `open-spaced-repetition/py-fsrs`, а не взяты
по памяти: одна перепутанная степень здесь — это не ошибка на день, а карточки,
которые молча приходят не тогда, и заметить это по ощущениям нельзя.

# ponytail: только дневная часть, без внутридневных шагов обучения (1 мин,
# 10 мин) — кот показывает карточки раз в день, а не гоняет сессию до
# запоминания. Функция короткой стабильности здесь есть и считается верно, но
# вызывается только когда два ответа пришлись на один день.

Веса можно переобучить под себя — на это есть оптимизатор в проекте FSRS, ему
нужна история ответов, и она в карточках лежит. Пока стоят те, что подобраны на
всех.
"""
from __future__ import annotations

import math

# FSRS-6, DEFAULT_PARAMETERS из py-fsrs. Последний — decay.
W = (0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722,
     0.1666, 0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425,
     0.0912, 0.0658, 0.1542)

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4

DECAY = -W[20]
FACTOR = 0.9 ** (1 / DECAY) - 1
STABILITY_MIN = 0.001
DIFFICULTY_MIN, DIFFICULTY_MAX = 1.0, 10.0
RETENTION = 0.9         # доля, которую хочется помнить к моменту показа
MAX_INTERVAL = 3650     # десять лет: дальше «помню» и «забыл» уже неразличимы


def retrievability(stability: float, elapsed_days: float) -> float:
    """Вероятность вспомнить прямо сейчас."""
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def interval(stability: float, retention: float = RETENTION) -> int:
    """Через сколько дней вспоминаемость упадёт до желаемой."""
    days = (stability / FACTOR) * (retention ** (1 / DECAY) - 1)
    return max(1, min(MAX_INTERVAL, round(days)))


def initial_stability(grade: int) -> float:
    return max(STABILITY_MIN, W[grade - 1])


def initial_difficulty(grade: int, clamp: bool = True) -> float:
    value = W[4] - math.e ** (W[5] * (grade - 1)) + 1
    return _clamp_d(value) if clamp else value


def next_difficulty(difficulty: float, grade: int) -> float:
    """Трудность ползёт к «лёгкой» — иначе она уезжает в 10 и не возвращается.

    Затухание `(10 − D)/9` гасит шаг тем сильнее, чем ближе к пределу, а
    подмешивание D₀(Сразу) тянет назад к среднему. Без обоих карточка, которую
    ты пару раз завалил, остаётся «трудной» навсегда.
    """
    delta = -(W[6] * (grade - 3))
    damped = difficulty + (DIFFICULTY_MAX - difficulty) * delta / 9.0
    reverted = W[7] * initial_difficulty(EASY, clamp=False) + (1 - W[7]) * damped
    return _clamp_d(reverted)


def short_term_stability(stability: float, grade: int) -> float:
    """Второй ответ в тот же день. Меньше стабильность не делает."""
    increase = (math.e ** (W[17] * (grade - 3 + W[18]))) * (stability ** -W[19])
    if grade >= HARD:
        increase = max(increase, 1.0)
    return max(STABILITY_MIN, stability * increase)


def recall_stability(difficulty: float, stability: float, r: float,
                     grade: int) -> float:
    hard_penalty = W[15] if grade == HARD else 1.0
    easy_bonus = W[16] if grade == EASY else 1.0
    grown = stability * (
        1 + (math.e ** W[8]) * (11 - difficulty) * (stability ** -W[9])
        * ((math.e ** ((1 - r) * W[10])) - 1) * hard_penalty * easy_bonus)
    return max(STABILITY_MIN, grown)


def forget_stability(difficulty: float, stability: float, r: float) -> float:
    long_term = (W[11] * (difficulty ** -W[12])
                 * (((stability + 1) ** W[13]) - 1)
                 * (math.e ** ((1 - r) * W[14])))
    short_term = stability / (math.e ** (W[17] * W[18]))
    return max(STABILITY_MIN, min(long_term, short_term))


def review(stability, difficulty, elapsed_days: float, grade: int) -> tuple:
    """Один ответ. (стабильность, трудность, интервал в днях).

    `stability is None` — карточку видят впервые, и состояние берётся из весов,
    а не считается из предыдущего, которого нет.
    """
    grade = max(AGAIN, min(EASY, int(grade)))
    if stability is None or difficulty is None:
        s = initial_stability(grade)
        d = initial_difficulty(grade)
        return s, d, interval(s)

    r = retrievability(float(stability), elapsed_days)
    d = next_difficulty(float(difficulty), grade)
    if elapsed_days < 1:
        # тот же день: это ещё не проверка памяти, а дозубривание
        s = short_term_stability(float(stability), grade)
    elif grade == AGAIN:
        s = forget_stability(float(difficulty), float(stability), r)
    else:
        s = recall_stability(float(difficulty), float(stability), r, grade)
    return s, d, interval(s)


def _clamp_d(value: float) -> float:
    return min(max(value, DIFFICULTY_MIN), DIFFICULTY_MAX)


def demo():
    # интервал при удержании 0.9 равен самой стабильности — это её определение,
    # и на нём ловится любая путаница в знаках DECAY и FACTOR
    for s in (1.0, 7.0, 63.5, 400.0):
        assert interval(s, 0.9) == round(s), (s, interval(s, 0.9))
    # вспоминаемость: единица в момент повтора, ровно удержание через S дней
    assert abs(retrievability(10, 0) - 1.0) < 1e-9
    assert abs(retrievability(10, 10) - 0.9) < 1e-6
    assert retrievability(10, 100) < retrievability(10, 10)

    # чем лучше первый ответ, тем дольше первый интервал. Первые два
    # совпадают — начальные стабильности 0.21 и 1.29 дня оба округляются
    # к полу в один день; в Anki на этом месте внутридневные шаги, которых
    # у кота нет
    firsts = [review(None, None, 0, g)[2] for g in (AGAIN, HARD, GOOD, EASY)]
    assert firsts == sorted(firsts) and firsts == [1, 1, 2, 8], firsts

    # трудность: «не помню» её поднимает, «сразу» опускает, и она не вылезает
    # за 1..10 сколько ни дави
    d = 5.0
    assert next_difficulty(d, AGAIN) > d > next_difficulty(d, EASY)
    hard = 5.0
    for _ in range(50):
        hard = next_difficulty(hard, AGAIN)
    assert hard <= DIFFICULTY_MAX
    easy = 5.0
    for _ in range(50):
        easy = next_difficulty(easy, EASY)
    assert easy >= DIFFICULTY_MIN

    # То, ради чего всё это: одно и то же слово, тот же ответ «вспомнил», но
    # разное ожидание. Вспомненное на грани забывания укрепляется сильнее —
    # ни Лейтнер, ни SM-2 этой разницы не видят вовсе.
    soon = review(10.0, 5.0, 1, GOOD)[0]
    late = review(10.0, 5.0, 20, GOOD)[0]
    assert late > soon * 1.5, (soon, late)

    # «Сразу» отодвигает дальше «вспомнил», «еле» — ближе, «не помню» роняет
    spans = [review(30.0, 5.0, 30, g)[2] for g in (AGAIN, HARD, GOOD, EASY)]
    assert spans == sorted(spans) and len(set(spans)) == 4, spans
    assert spans[0] < 30 < spans[2], spans

    # «Вспомнил» подряд с шагом в назначенный интервал: последовательность
    # должна расти, а не топтаться
    s, d, days = review(None, None, 0, GOOD)
    seen = [days]
    for _ in range(6):
        s, d, days = review(s, d, days, GOOD)
        seen.append(days)
    assert seen == sorted(seen) and seen[-1] > 100, seen
    print("ok", seen)


if __name__ == "__main__":
    demo()
