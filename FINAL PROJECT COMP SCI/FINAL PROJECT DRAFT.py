"""
Texas Hold'em Poker -- a Drafter website
========================================
Play one human against four computer opponents (5 players at the table).

Run:   pip install drafter
       python poker_site.py

Structure
---------
* Two dataclasses: Player and State (State holds players, community_cards,
  pot and current_turn_index, plus a few supporting fields the game needs).
* Seven required routes: index, start_new_game, deal_hand, player_action,
  trigger_bot_turns, advance_street, reset_stack
  (plus one small navigation route, show_rules, so the Rules page is reachable).
* Five pages: welcome/setup, main table, showdown/hand results,
  game over/rebuy, rules & hand rankings.
"""
from dataclasses import dataclass, field
from itertools import combinations
from collections import Counter
from typing import List, Optional, Tuple
import random

from drafter import *

try:                                     # used to color-code the card suits
    from drafter import Span, change_color
except ImportError:                      # older Drafter: cards fall back to plain text
    Span = None
    change_color = None

import drafter as _drafter              # optional layout / image / styling helpers
Image = getattr(_drafter, "Image", None)
Div = getattr(_drafter, "Div", None)
change_background_color = getattr(_drafter, "change_background_color", None)
change_padding = getattr(_drafter, "change_padding", None)
change_text_align = getattr(_drafter, "change_text_align", None)
change_border = getattr(_drafter, "change_border", None)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RANKS = "23456789TJQKA"
SUITS = "♠♥♦♣"

RANK_NAMES = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
              8: "Eight", 9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen",
              13: "King", 14: "Ace"}
RANK_PLURALS = {2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes",
                7: "Sevens", 8: "Eights", 9: "Nines", 10: "Tens", 11: "Jacks",
                12: "Queens", 13: "Kings", 14: "Aces"}

BETTING_STAGES = ("preflop", "flop", "turn", "river")
STAGE_LABELS = {"setup": "Setup", "preflop": "Pre-flop", "flop": "Flop",
                "turn": "Turn", "river": "River", "showdown": "Showdown",
                "gameover": "Game Over"}

NUM_PLAYERS = 5          # you + four computer opponents

# How the computer opponents think, by difficulty.
#   sims         - Monte Carlo simulations used to estimate their winning chances
#   noise        - random error added to that estimate after the flop (bigger = sloppier)
#   pre_noise    - random error in their pre-flop hand rating
#   raise_k      - how conservative they are about raising (higher = needs a stronger hand)
#   fold_margin  - they fold when win chance < pot odds * fold_margin (lower = calls too much)
#   bluff        - how often they bluff / semi-bluff
#   cbet         - how often the pre-flop raiser bets the flop
#   trap         - how often they slow-play a monster
#   semi         - how often they semi-bluff raise a draw
#   reads        - whether they use what they have learned about each player's raising habits
#   read_floor   - hand strength they assume a player who raised is holding (0 = no reading)
#   position     - whether they play tighter early and looser on the button
#   pre_tight    - shifts how many hands they open pre-flop (negative = looser)
#   raise_extra  - extra hand strength needed to open-raise instead of limping
#   limp_gap     - how much weaker than an open they are willing to limp with
#   call_loose   - extra looseness when calling a pre-flop raise
#   threebet     - hand strength (Chen score) needed to re-raise pre-flop
#   bluff3       - chance of a light re-raise bluff pre-flop (late position)
BOT_PROFILES = {
    "Easy":   {"sims": 15, "noise": 0.12, "pre_noise": 1.5, "raise_k": 0.55, "fold_margin": 0.65,
               "bluff": 0.02, "cbet": 0.10, "trap": 0.00, "semi": 0.00, "reads": False,
               "read_floor": 0.0, "position": False, "pre_tight": -1.0, "raise_extra": 2.5,
               "limp_gap": 3.0, "call_loose": 2.0, "threebet": 13.0, "bluff3": 0.00},
    "Medium": {"sims": 45, "noise": 0.04, "pre_noise": 0.6, "raise_k": 0.45, "fold_margin": 0.95,
               "bluff": 0.06, "cbet": 0.40, "trap": 0.10, "semi": 0.04, "reads": True,
               "read_floor": 5.0, "position": True, "pre_tight": 0.0, "raise_extra": 0.5,
               "limp_gap": 1.5, "call_loose": 0.0, "threebet": 11.5, "bluff3": 0.00},
    "Hard":   {"sims": 100, "noise": 0.00, "pre_noise": 0.0, "raise_k": 0.38, "fold_margin": 1.05,
               "bluff": 0.10, "cbet": 0.55, "trap": 0.25, "semi": 0.08, "reads": True,
               "read_floor": 6.5, "position": True, "pre_tight": 0.0, "raise_extra": 0.0,
               "limp_gap": 1.0, "call_loose": 0.0, "threebet": 11.0, "bluff3": 0.06},
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Player:
    name: str                                   # set once at setup, read-only in play
    chips: int                                  # remaining stack
    hole_cards: List[str] = field(default_factory=list)   # private 2-card hand
    current_bet: int = 0                        # chips put in during this betting round
    is_folded: bool = False                     # True once the player folds


@dataclass
class State:
    # --- the four required attributes -------------------------------------
    players: List[Player] = field(default_factory=list)
    community_cards: List[str] = field(default_factory=list)
    pot: int = 0
    current_turn_index: int = 0

    # --- supporting game data ---------------------------------------------
    stage: str = "setup"            # setup / preflop / flop / turn / river / showdown / gameover
    deck: List[str] = field(default_factory=list)
    dealer: int = 2
    hand_number: int = 0
    small_blind: int = 5
    big_blind: int = 10
    min_raise: int = 10
    raises_this_street: int = 0
    acted: List[bool] = field(default_factory=list)       # who has acted this round
    contributed: List[int] = field(default_factory=list)  # chips each player put in this hand
    starting_stack: int = 1000
    difficulty: str = "Medium"
    auto_muck: bool = False
    message: str = ""
    action_log: List[str] = field(default_factory=list)

    # --- what the AI opponents observe ----------------------------------------
    raised_flags: List[bool] = field(default_factory=list)   # who has raised this hand
    preflop_aggressor: int = -1                               # last player to raise pre-flop
    opp_raises: List[int] = field(default_factory=list)       # session raise count per player
    opp_actions: List[int] = field(default_factory=list)      # session action count per player

    # --- results of the last hand -------------------------------------------
    result_headline: str = ""
    reveal: bool = False
    hand_names: List[str] = field(default_factory=list)
    winnings: List[int] = field(default_factory=list)
    winning_cards: List[str] = field(default_factory=list)
    winning_hand_name: str = ""
    last_pot: int = 0

    # --- session statistics -------------------------------------------------
    hands_played: int = 0
    hands_won: int = 0
    biggest_pot: int = 0
    rebuys: int = 0
    peak_chips: int = 0


# ---------------------------------------------------------------------------
# Cards and hand evaluation
# ---------------------------------------------------------------------------
def new_deck() -> List[str]:
    deck = [r + s for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def show_card(card: str) -> str:
    """'T♠' -> '10♠' for display."""
    return card.replace("T", "10")


SUIT_COLORS = {"♦": "#ff8c00",    # diamonds - orange
               "♥": "#d40000",    # hearts   - red
               "♣": "#1b8a1b",    # clubs    - green
               "♠": "#0a1f6e"}    # spades   - dark blue


def styled(text: str, color: str):
    """Colored text; falls back to plain text if this Drafter has no Span/change_color."""
    if Span is None or change_color is None:
        return text
    return change_color(Span(text), color)


def rich(*parts):
    """Combine strings and styled pieces into one line."""
    if all(isinstance(p, str) for p in parts):
        return "".join(parts)
    return Span(*parts)


def card_piece(card: str):
    return styled("[" + show_card(card) + "]", SUIT_COLORS[card[1]])


def colorize(text: str):
    """Color every card token such as 'A♠' or '10♦' inside a plain string."""
    parts = []
    for i, token in enumerate(text.split(" ")):
        if i:
            parts.append(" ")
        if token and token[-1] in SUIT_COLORS:
            parts.append(styled(token, SUIT_COLORS[token[-1]]))
        else:
            parts.append(token)
    return rich(*parts)


def cards_text(cards: List[str]):
    parts = []
    for i, c in enumerate(cards):
        if i:
            parts.append(" ")
        parts.append(card_piece(c))
    return rich(*parts)


def board_text(cards: List[str]):
    parts = []
    for i in range(5):
        if i:
            parts.append(" ")
        parts.append(card_piece(cards[i]) if i < len(cards) else "[  ]")
    return rich(*parts)


# ---------------------------------------------------------------------------
# Card images and the table layout
# ---------------------------------------------------------------------------
# The images live in an "images" folder next to this file (create them with
# make_card_images.py).  images/cards/AS.png = ace of spades, TH.png = ten of hearts, ...
IMAGE_DIR = "images"
SUIT_LETTER = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
FELT = "#0b5d34"          # table cloth
SEAT_BG = "#14703f"       # a player's seat
ACTING_BG = "#ffe27a"     # whose turn it is
WINNER_BG = "#ffd24a"     # winner of the hand
FOLDED_BG = "#4a5a52"     # folded players


def card_file(card: str) -> str:
    return IMAGE_DIR + "/cards/" + card[0] + SUIT_LETTER[card[1]] + ".png"


def card_image(card: str, width: int = 62):
    """A picture of the card (plain colored text if images are unavailable)."""
    if Image is None:
        return card_piece(card)
    return Image(card_file(card), width=width)


def back_image(width: int = 62):
    return Image(IMAGE_DIR + "/back.png", width=width) if Image is not None else "[??]"


def empty_image(width: int = 62):
    return Image(IMAGE_DIR + "/empty.png", width=width) if Image is not None else "[  ]"


def badge_image(kind: str, width: int = 30):
    """kind is 'dealer', 'small_blind' or 'big_blind'."""
    return Image(IMAGE_DIR + "/badges/" + kind + ".png", width=width) if Image is not None else None


def decorate(component, bg=None, color=None, pad=None, align=None, border=None):
    """Apply background / text color / padding when this Drafter supports it."""
    try:
        if bg and change_background_color:
            component = change_background_color(component, bg)
        if color and change_color:
            component = change_color(component, color)
        if pad and change_padding:
            component = change_padding(component, pad)
        if align and change_text_align:
            component = change_text_align(component, align)
        if border and change_border:
            component = change_border(component, border)
    except Exception:
        pass
    return component


def box(items, **style):
    """A block of components (Div) with optional styling."""
    if Div is None:
        return " ".join(i for i in items if isinstance(i, str))
    return decorate(Div(*items), **style)


def card_row(cards: List[str], width: int = 62, hidden: bool = False, slots: int = 0) -> list:
    """Components for a row of cards: faces, or card backs when hidden."""
    row = []
    for c in cards:
        if row:
            row.append(" ")
        row.append(back_image(width) if hidden else card_image(c, width))
    for _ in range(slots - len(cards)):
        if row:
            row.append(" ")
        row.append(empty_image(width))
    return row


def seat_roles(state: State, i: int) -> List[str]:
    """The rotating jobs: dealer button, small blind, big blind."""
    n = len(state.players)
    roles = []
    if i == state.dealer:
        roles.append("dealer")
    if i == (state.dealer + 1) % n:
        roles.append("small_blind")
    if i == (state.dealer + 2) % n:
        roles.append("big_blind")
    return roles


ROLE_LABEL = {"dealer": "Dealer", "small_blind": "Small Blind", "big_blind": "Big Blind"}


def seat_box(state: State, i: int, reveal: bool = False):
    """One player's seat: name, role badges, chips, bet, status and cards (as images)."""
    p = state.players[i]
    in_betting = state.stage in BETTING_STAGES
    won = state.winnings[i] if (state.stage == "showdown" and i < len(state.winnings)) else 0
    acting = in_betting and i == state.current_turn_index

    items = []
    for role in seat_roles(state, i):
        badge = badge_image(role)
        if badge is not None:
            items.append(badge)
            items.append(" ")
    items.append(p.name + (" (you)" if i == 0 else ""))
    items.append(LineBreak())
    roles = [ROLE_LABEL[r] for r in seat_roles(state, i)]
    if roles:
        items.append(" / ".join(roles))
        items.append(LineBreak())
    items.append("Chips: " + str(p.chips) + (("   Bet: " + str(p.current_bet)) if in_betting else ""))
    items.append(LineBreak())

    if p.is_folded:
        status = "Folded"
    elif won > 0:
        status = "★ WINNER +" + str(won)
    elif acting:
        status = "▶ Your turn" if i == 0 else "▶ Acting"
    elif p.chips == 0 and in_betting:
        status = "All-in"
    else:
        status = "In hand" if in_betting else ""
    if status:
        items.append(status)
        items.append(LineBreak())

    if p.hole_cards and not p.is_folded:
        mucked = reveal and i == 0 and won == 0 and state.auto_muck
        face_up = (i == 0 and not mucked) or (reveal and i != 0)
        items.extend(card_row(p.hole_cards, 62, hidden=not face_up))

    if p.is_folded:
        bg, fg = FOLDED_BG, "#d0d8d4"
    elif won > 0:
        bg, fg = WINNER_BG, "#1a1a1a"
    elif acting:
        bg, fg = ACTING_BG, "#1a1a1a"
    else:
        bg, fg = SEAT_BG, "white"
    return box(items, bg=bg, color=fg, pad="8px", align="center", border="2px solid #e8d9a8")


def table_layout(state: State, reveal: bool = False):
    """The five players seated around the table; the pot and community cards sit in the middle.

              Bot 2        Pot         Bot 3
              Bot 1     Community      Bot 4
                         You (seat 0)
    """
    pot_box = box([
        "Pot: " + str(state.pot if state.stage in BETTING_STAGES else state.last_pot) + " chips",
        LineBreak(),
        STAGE_LABELS.get(state.stage, state.stage) + "  -  Blinds " + str(state.small_blind) + "/" + str(state.big_blind),
    ], bg=FELT, color="white", pad="6px", align="center")

    middle = card_row(state.community_cards, 70, slots=5)
    board_box = box(middle, bg=FELT, color="white", pad="8px", align="center")
    blank = box([" "], bg=FELT)
    seats = {i: seat_box(state, i, reveal) for i in range(len(state.players))}
    rows = [
        [seats[2], pot_box, seats[3]],
        [seats[1], board_box, seats[4]],
        [blank, seats[0], blank],
    ]
    return decorate(Table(rows), bg=FELT, pad="10px", align="center")


def score_five(cards) -> Tuple[int, ...]:
    """Score exactly five cards. Higher tuples beat lower tuples.
    Category: 0 high card, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush,
    6 full house, 7 quads, 8 straight flush, 9 royal flush."""
    ranks = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    counts = Counter(ranks)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [n for _, n in groups]
    ordered = [r for r, _ in groups]

    straight_high = 0
    if len(counts) == 5:
        if ranks[0] - ranks[4] == 4:
            straight_high = ranks[0]
        elif ranks == [14, 5, 4, 3, 2]:        # the "wheel": A-2-3-4-5
            straight_high = 5

    if straight_high and flush:
        return (9 if straight_high == 14 else 8, straight_high)
    if shape == [4, 1]:
        return (7, *ordered)
    if shape == [3, 2]:
        return (6, *ordered)
    if flush:
        return (5, *ranks)
    if straight_high:
        return (4, straight_high)
    if shape == [3, 1, 1]:
        return (3, *ordered)
    if shape == [2, 2, 1]:
        return (2, *ordered)
    if shape == [2, 1, 1, 1]:
        return (1, *ordered)
    return (0, *ranks)


def best_score(cards) -> Tuple[int, ...]:
    """Best 5-card score from 5-7 cards (fast; used by the bots)."""
    best = None
    for five in combinations(cards, 5):
        s = score_five(five)
        if best is None or s > best:
            best = s
    return best


def describe_hand(score: Tuple[int, ...]) -> str:
    cat = score[0]
    if cat == 9:
        return "Royal Flush"
    if cat == 8:
        return "Straight Flush, " + RANK_NAMES[score[1]] + " high"
    if cat == 7:
        return "Four of a Kind, " + RANK_PLURALS[score[1]]
    if cat == 6:
        return "Full House, " + RANK_PLURALS[score[1]] + " full of " + RANK_PLURALS[score[2]]
    if cat == 5:
        return "Flush, " + RANK_NAMES[score[1]] + " high"
    if cat == 4:
        return "Straight, " + RANK_NAMES[score[1]] + " high"
    if cat == 3:
        return "Three of a Kind, " + RANK_PLURALS[score[1]]
    if cat == 2:
        return "Two Pair, " + RANK_PLURALS[score[1]] + " and " + RANK_PLURALS[score[2]]
    if cat == 1:
        return "Pair of " + RANK_PLURALS[score[1]]
    return "High Card, " + RANK_NAMES[score[1]]


def best_hand(cards) -> Tuple[Tuple[int, ...], List[str], str]:
    """Return (score, the best five cards in display order, hand name)."""
    best = None
    best_five = None
    for five in combinations(cards, 5):
        s = score_five(five)
        if best is None or s > best:
            best, best_five = s, list(five)
    counts = Counter(c[0] for c in best_five)
    best_five.sort(key=lambda c: (counts[c[0]], RANKS.index(c[0])), reverse=True)
    if best[0] in (4, 8) and best[1] == 5:     # wheel: show the Ace last
        best_five.append(best_five.pop(0))
    return best, best_five, describe_hand(best)


def chen_score(cards) -> float:
    """The Chen formula: a quick pre-flop rating, roughly -1 (junk) to 20 (pocket aces)."""
    r1, r2 = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    top = {14: 10.0, 13: 8.0, 12: 7.0, 11: 6.0}.get(r1, r1 / 2.0)
    if r1 == r2:
        return max(5.0, top * 2)
    score = top
    if cards[0][1] == cards[1][1]:
        score += 2                                   # suited
    gap = r1 - r2 - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and r1 < 12:
        score += 1                                   # connected, below a queen
    return score


def estimate_equity(hole: List[str], board: List[str], floors: List[float], sims: int) -> float:
    """Monte Carlo estimate of the chance this hand wins at showdown.
    floors has one entry per opponent still in the hand: 0 means that opponent could hold
    anything; a higher number means they raised, so their hand is drawn from stronger
    holdings (Chen score at least that high)."""
    seen = set(hole) | set(board)
    deck = [r + s for r in RANKS for s in SUITS if (r + s) not in seen]
    need = 5 - len(board)
    wins = 0.0
    for _ in range(sims):
        d = deck[:]
        random.shuffle(d)
        full_board = board + d[:need]
        rest = d[need:]
        mine = best_score(hole + full_board)
        best_opp = None
        for floor in floors:
            picked = None
            if floor > 0:
                for _try in range(12):
                    i, j = random.sample(range(len(rest)), 2)
                    if chen_score((rest[i], rest[j])) >= floor:
                        picked = (i, j)
                        break
            if picked is None:
                picked = (len(rest) - 1, len(rest) - 2)
            hand = [rest[picked[0]], rest[picked[1]]]
            for k in sorted(picked, reverse=True):
                rest.pop(k)
            sc = best_score(hand + full_board)
            if best_opp is None or sc > best_opp:
                best_opp = sc
        if mine > best_opp:
            wins += 1
        elif mine == best_opp:
            wins += 0.5
    return wins / sims


# ---------------------------------------------------------------------------
# Betting-round helpers
# ---------------------------------------------------------------------------
def can_act(player: Player) -> bool:
    """Still in the hand and has chips left (folded and all-in players cannot act)."""
    return (not player.is_folded) and player.chips > 0


def highest_bet(state: State) -> int:
    top = 0
    for p in state.players:
        if not p.is_folded and p.current_bet > top:
            top = p.current_bet
    return top


def next_actor(state: State) -> int:
    """Increment the turn index with modulo wrap-around, skipping players who can't act."""
    n = len(state.players)
    for step in range(1, n + 1):
        candidate = (state.current_turn_index + step) % n
        if can_act(state.players[candidate]):
            return candidate
    return state.current_turn_index


def betting_round_complete(state: State) -> bool:
    """THE LOOP: walk through state.players and decide whether every active player
    has matched the highest bet (or is all-in), and has had a chance to act."""
    highest = highest_bet(state)
    able = 0          # players who can still act
    waiting = 0       # of those, how many have not acted yet this round
    for i, p in enumerate(state.players):
        if p.is_folded or p.chips == 0:
            continue                        # folded, or all-in: nothing more to do
        if p.current_bet != highest:
            return False                    # still owes chips to the pot
        able += 1
        if not state.acted[i]:
            waiting += 1
    if able >= 2 and waiting > 0:
        return False                        # someone still has their turn (e.g. big blind option)
    return True


def add_log(state: State, text: str) -> None:
    state.action_log.append(text)


def put_chips(state: State, idx: int, amount: int) -> None:
    p = state.players[idx]
    amount = min(amount, p.chips)
    p.chips -= amount
    p.current_bet += amount
    state.pot += amount
    state.contributed[idx] += amount


def post_blind(state: State, idx: int, amount: int, label: str) -> None:
    put_chips(state, idx, amount)
    p = state.players[idx]
    add_log(state, p.name + " posts the " + label + " (" + str(p.current_bet) + ")")


def apply_action(state: State, idx: int, action: str, raise_by: int = 0) -> None:
    """Carry out 'fold', 'call' (check/call) or 'raise' for the player at idx."""
    p = state.players[idx]
    to_call = max(0, highest_bet(state) - p.current_bet)
    state.opp_actions[idx] += 1                     # the AI keeps score of everyone's habits
    if action == "fold":
        p.is_folded = True
        add_log(state, p.name + " folds")
    elif action == "raise":
        put_chips(state, idx, to_call + raise_by)
        state.min_raise = max(state.min_raise, raise_by)
        state.raises_this_street += 1
        state.opp_raises[idx] += 1
        state.raised_flags[idx] = True
        if state.stage == "preflop":
            state.preflop_aggressor = idx
        for j in range(len(state.players)):         # everyone else must respond
            if j != idx:
                state.acted[j] = False
        note = " (all-in)" if p.chips == 0 else ""
        add_log(state, p.name + " raises to " + str(p.current_bet) + note)
    else:
        paid = min(to_call, p.chips)
        put_chips(state, idx, paid)
        if to_call == 0:
            add_log(state, p.name + " checks")
        else:
            note = " (all-in)" if p.chips == 0 else ""
            add_log(state, p.name + " calls " + str(paid) + note)
    state.acted[idx] = True


# ---------------------------------------------------------------------------
# Computer opponent decision making
# ---------------------------------------------------------------------------
def raise_rate(state: State, j: int) -> float:
    """How often player j has raised this session (0.25 until there is enough data)."""
    actions = state.opp_actions[j]
    return state.opp_raises[j] / actions if actions >= 8 else 0.25


def bot_context(state: State, idx: int, profile: dict) -> dict:
    """Facts the bot at idx uses for its decision."""
    bot = state.players[idx]
    n = len(state.players)
    highest = highest_bet(state)
    to_call = max(0, highest - bot.current_bet)
    room = bot.chips - to_call                      # chips left after calling
    opps = [j for j, p in enumerate(state.players) if j != idx and not p.is_folded]

    # Position: 0 = first to act after the flop (small blind) ... 1 = the dealer button.
    lateness = ((idx - state.dealer - 1) % n) / (n - 1) if profile["position"] else 0.5

    # Reading the player who made the biggest bet: tight players' bets are respected,
    # loose-aggressive players' bets are called more lightly.
    agg, agg_scale = 0.0, 1.0
    if profile["reads"] and to_call > 0:
        aggressor = max(opps, key=lambda j: state.players[j].current_bet)
        rate = raise_rate(state, aggressor)
        if rate < 0.15:
            agg, agg_scale = 1.5, 1.15
        elif rate > 0.40:
            agg, agg_scale = -1.0, 0.92

    return {"highest": highest, "to_call": to_call, "room": room, "opps": opps,
            "lateness": lateness, "agg": agg, "agg_scale": agg_scale,
            "can_raise": room >= state.min_raise and state.raises_this_street < 3}


def make_raise(state: State, ctx: dict, raise_by: int) -> Tuple[str, int]:
    if not ctx["can_raise"]:
        return ("call", 0)
    raise_by = max(int(raise_by), state.min_raise)
    return ("raise", min(raise_by, ctx["room"]))


def pot_raise(state: State, ctx: dict, fraction: float) -> Tuple[str, int]:
    """Bet or raise a fraction of the pot (after calling)."""
    size = int((state.pot + ctx["to_call"]) * fraction)
    size = (size // state.small_blind) * state.small_blind
    return make_raise(state, ctx, size)


def raise_to(state: State, ctx: dict, target_total: float) -> Tuple[str, int]:
    """Raise so that the highest bet becomes target_total."""
    return make_raise(state, ctx, int(target_total) - ctx["highest"])


def shove(ctx: dict) -> Tuple[str, int]:
    return ("raise", ctx["room"]) if ctx["can_raise"] else ("call", 0)


def preflop_action(state: State, idx: int, profile: dict, ctx: dict) -> Tuple[str, int]:
    """Pre-flop: a position-aware starting-hand chart (Chen score)."""
    bot = state.players[idx]
    bb = state.big_blind
    to_call, highest = ctx["to_call"], ctx["highest"]
    n_active = len(ctx["opps"]) + 1
    late = ctx["lateness"]

    chen = chen_score(bot.hole_cards)
    if profile["pre_noise"] > 0:
        chen += random.gauss(0, profile["pre_noise"])
    # Open-raise threshold: tighter in early position and when many players are in.
    open_thr = 7.5 - 3.0 * late + 0.3 * (n_active - 3) + profile["pre_tight"]
    stack_bb = (bot.chips + bot.current_bet) / bb
    raised_pot = highest > bb
    check_or_fold = ("call", 0) if to_call == 0 else ("fold", 0)

    if stack_bb <= 10:                              # short stack: push or fold
        push_thr = 9.5 if raised_pot else 6.0
        return shove(ctx) if chen >= push_thr else check_or_fold

    if not raised_pot:                              # nobody has raised yet
        limpers = max(0, sum(1 for j in ctx["opps"] if state.players[j].current_bet >= bb) - 1)
        if chen >= open_thr + profile["raise_extra"]:
            return raise_to(state, ctx, bb * (3 + limpers))
        if to_call == 0 or (chen >= open_thr - profile["limp_gap"] and to_call <= bb):
            return ("call", 0)                      # check, or limp in cheaply
        return ("fold", 0)

    # Facing a raise.
    is_bb = idx == (state.dealer + 2) % len(state.players)
    price = to_call / (state.pot + to_call)
    premium = 13.5 if state.raises_this_street >= 2 else profile["threebet"] + ctx["agg"]
    if chen >= premium:                             # re-raise (smaller when in position)
        return raise_to(state, ctx, highest * (3 if late >= 0.5 else 3.5))
    if (state.raises_this_street == 1 and late >= 0.75 and 6 <= chen < premium
            and random.random() < profile["bluff3"]):
        return raise_to(state, ctx, highest * 3)    # light re-raise bluff from late position

    call_thr = open_thr + 0.8 + 0.6 * max(0.0, to_call / bb - 2) + ctx["agg"] - profile["call_loose"]
    if price < 0.2:
        call_thr -= 1.5                             # cheap price
    if is_bb and to_call <= 3 * bb:
        call_thr -= 1.5                             # defend the big blind
    if to_call >= 0.4 * bot.chips:
        call_thr = max(call_thr, 11.0)              # risking a big chunk of the stack
    call_thr = min(call_thr, 14.0)
    return ("call", 0) if chen >= call_thr else ("fold", 0)


def postflop_action(state: State, idx: int, profile: dict, ctx: dict) -> Tuple[str, int]:
    """Flop / turn / river: simulated winning chances against the opponents' likely hands."""
    bot = state.players[idx]
    to_call, room, late = ctx["to_call"], ctx["room"], ctx["lateness"]
    opps = ctx["opps"]
    n_opp = len(opps)

    # Opponents who raised this hand are assumed to hold stronger cards than random.
    floors = []
    for j in opps:
        floor = 0.0
        if profile["read_floor"] > 0 and state.raised_flags[j]:
            rate = raise_rate(state, j)
            floor = profile["read_floor"] + (1.5 if rate < 0.15 else (-1.5 if rate > 0.40 else 0.0))
        floors.append(floor)
    equity = estimate_equity(bot.hole_cards, state.community_cards, floors, profile["sims"])
    if profile["noise"] > 0:
        equity += random.gauss(0, profile["noise"])

    fair = 1.0 / (n_opp + 1)                        # an average hand's share
    raise_at = fair + profile["raise_k"] * (1.0 - fair)
    pot_odds = to_call / (state.pot + to_call) if to_call > 0 else 0.0
    need = pot_odds * profile["fold_margin"] * ctx["agg_scale"]
    if profile["reads"] and to_call > 0 and state.stage in ("flop", "turn") and room > 2 * state.pot:
        need -= 0.03                                # implied odds with deep stacks
    if to_call > 0 and to_call >= 0.6 * bot.chips:
        need = min(need, pot_odds)                  # already committed: just price-check
    check_or_fold = ("call", 0) if to_call == 0 else ("fold", 0)

    # Short stack: shove or fold.
    if (bot.chips + bot.current_bet) / state.big_blind <= 8:
        if equity >= raise_at - 0.08:
            return shove(ctx)
        return ("call", 0) if (to_call == 0 or equity >= need) else check_or_fold

    if to_call == 0:                                # checked to us
        if equity >= raise_at:
            if equity > 0.88 and state.stage != "river" and random.random() < profile["trap"]:
                return ("call", 0)                  # slow-play a monster
            fraction = 0.5 if equity < 0.65 else (0.7 if equity < 0.8 else 1.0)
            return pot_raise(state, ctx, fraction)
        if (state.stage == "flop" and state.preflop_aggressor == idx and n_opp <= 2
                and random.random() < profile["cbet"]):
            return pot_raise(state, ctx, 0.5)       # continuation bet
        if n_opp <= 2 and 0.2 <= equity < raise_at and random.random() < profile["bluff"] * (0.5 + late):
            return pot_raise(state, ctx, 0.55)      # semi-bluff / steal, best in late position
        return ("call", 0)                          # check

    # Facing a bet.
    if equity < need:
        if (state.stage != "river" and n_opp == 1
                and random.random() < profile["bluff"] * 0.4):
            return pot_raise(state, ctx, 0.8)       # occasional bluff-raise
        return ("fold", 0)
    strong = equity >= raise_at + 0.05
    if state.raises_this_street >= 2:
        strong = equity >= 0.85                     # only re-raise a re-raise with a monster
    if strong:
        return pot_raise(state, ctx, 0.75 if equity < 0.8 else 1.0)
    if (state.stage in ("flop", "turn") and n_opp == 1 and 0.3 <= equity < raise_at
            and random.random() < profile["semi"]):
        return pot_raise(state, ctx, 0.75)          # semi-bluff raise with a draw
    return ("call", 0)


def choose_bot_action(state: State, idx: int) -> Tuple[str, int]:
    """Return ('fold' | 'call' | 'raise', raise_by) for the bot at idx."""
    profile = BOT_PROFILES.get(state.difficulty, BOT_PROFILES["Medium"])
    ctx = bot_context(state, idx, profile)
    if state.stage == "preflop":
        return preflop_action(state, idx, profile, ctx)
    return postflop_action(state, idx, profile, ctx)


# ---------------------------------------------------------------------------
# Pot awarding
# ---------------------------------------------------------------------------
def build_side_pots(state: State) -> List[Tuple[int, List[int]]]:
    """Split the pot into layers by how much each player put in.
    Returns [(amount, [eligible player indexes]), ...]."""
    pots = []
    previous = 0
    for level in sorted({c for c in state.contributed if c > 0}):
        contributors = [i for i, c in enumerate(state.contributed) if c >= level]
        amount = (level - previous) * len(contributors)
        eligible = [i for i in contributors if not state.players[i].is_folded]
        if not eligible:
            eligible = [i for i, p in enumerate(state.players) if not p.is_folded]
        pots.append((amount, eligible))
        previous = level
    return pots


def finish_hand(state: State, winnings: List[int], reveal: bool, headline: str,
                hand_names: List[str], winning_cards: List[str], winning_name: str) -> Page:
    """Pay out, reset the pot to 0, record results/statistics, show the results page."""
    total = state.pot
    for i, amount in enumerate(winnings):
        state.players[i].chips += amount
    state.pot = 0
    state.winnings = winnings
    state.reveal = reveal
    state.result_headline = headline
    state.hand_names = hand_names
    state.winning_cards = winning_cards
    state.winning_hand_name = winning_name
    state.last_pot = total
    state.stage = "showdown"
    state.hands_played += 1
    if winnings[0] > 0:
        state.hands_won += 1
        state.biggest_pot = max(state.biggest_pot, total)
    state.peak_chips = max(state.peak_chips, state.players[0].chips)
    return showdown_page(state)


def award_uncontested(state: State, winner_idx: int) -> Page:
    """Everyone else folded: skip the remaining cards and pay the last player."""
    n = len(state.players)
    winnings = [0] * n
    winnings[winner_idx] = state.pot
    winner = state.players[winner_idx]
    headline = "★ " + winner.name + " wins " + str(state.pot) + " chips - everyone else folded ★"
    return finish_hand(state, winnings, False, headline, [""] * n, [], "")


def resolve_showdown(state: State) -> Page:
    """Compare the remaining hands, pay out main/side pots, show the results page."""
    n = len(state.players)
    scored = {}
    for i, p in enumerate(state.players):
        if not p.is_folded:
            scored[i] = best_hand(p.hole_cards + state.community_cards)

    winnings = [0] * n
    for amount, eligible in build_side_pots(state):
        in_running = [i for i in eligible if i in scored]
        top = max(scored[i][0] for i in in_running)
        winners = [i for i in in_running if scored[i][0] == top]
        share, extra = divmod(amount, len(winners))
        for w in winners:
            winnings[w] += share
        winnings[winners[0]] += extra               # odd chip goes to the first winner

    hand_names = [scored[i][2] if i in scored else "" for i in range(n)]
    best_idx = max(scored, key=lambda i: scored[i][0])
    best_score_value = scored[best_idx][0]
    top_players = [i for i in scored if scored[i][0] == best_score_value]
    paid = [i for i in range(n) if winnings[i] > 0]

    best_name = scored[best_idx][2]
    if len(paid) == 1:
        headline = "★ " + state.players[paid[0]].name + " wins " + str(state.pot) + " chips with " + best_name + " ★"
    elif set(paid) == set(top_players):
        names = " and ".join(state.players[i].name for i in paid)
        headline = "★ Split pot: " + names + " tie with " + best_name + " ★"
    else:                                           # main pot and side pot(s) went to different players
        parts = [state.players[i].name + " wins " + str(winnings[i]) for i in paid]
        headline = "★ " + ", ".join(parts) + " (best hand: " + best_name + ") ★"
    return finish_hand(state, winnings, True, headline, hand_names, scored[best_idx][1], scored[best_idx][2])


def check_progress(state: State) -> Optional[Page]:
    """After any action (or at the start of a round) decide what happens next.
    Returns a Page if the hand moved on, or None if the betting continues."""
    # IF 3 - Evaluating Uncontested Winner (fold detection)
    active = [i for i, p in enumerate(state.players) if not p.is_folded]
    if len(active) == 1:
        return award_uncontested(state, active[0])

    # IF 2 - Determining Hand Advancement vs. Showdown
    if betting_round_complete(state):
        if len(state.community_cards) < 5:
            return advance_street(state)
        return resolve_showdown(state)
    return None


# ---------------------------------------------------------------------------
# Routes (the seven required functions, plus show_rules for navigation)
# ---------------------------------------------------------------------------
@route
def index(state: State) -> Page:
    """Renders the main game table and controls (or the page for the current phase)."""
    if state.stage == "setup":
        return welcome_page(state)
    if state.stage == "showdown":
        return showdown_page(state)
    if state.stage == "gameover":
        return game_over_page(state)
    return table_page(state)


@route
def start_new_game(state: State, player_name: str, starting_stack: str, difficulty: str) -> Page:
    """Reset stacks, clear the community cards, and set up a fresh deck."""
    try:
        stack = int(starting_stack)
    except ValueError:
        stack = 1000
    name = player_name.strip() or "User"

    state.starting_stack = stack
    state.difficulty = difficulty if difficulty in BOT_PROFILES else "Medium"
    state.big_blind = max(2, (stack // 50) // 2 * 2)
    state.small_blind = state.big_blind // 2
    state.min_raise = state.big_blind

    state.players = [Player(name, stack)] + [Player("Bot " + str(k), stack) for k in range(1, NUM_PLAYERS)]
    n = len(state.players)
    state.community_cards = []
    state.pot = 0
    state.current_turn_index = 0
    state.deck = new_deck()
    state.dealer = n - 1                           # so the first hand's dealer is seat 0
    state.hand_number = 0
    state.acted = [False] * n
    state.contributed = [0] * n
    state.raised_flags = [False] * n
    state.preflop_aggressor = -1
    state.opp_raises = [0] * n                     # the AI's notes on everyone's habits
    state.opp_actions = [0] * n
    state.action_log = []
    state.message = ""
    state.hands_played = 0
    state.hands_won = 0
    state.biggest_pot = 0
    state.rebuys = 0
    state.peak_chips = stack
    return deal_hand(state)


@route
def deal_hand(state: State) -> Page:
    """Deal 2 hole cards to each player and post the small and big blinds."""
    human = state.players[0]
    if human.chips <= 0:
        state.stage = "gameover"
        return game_over_page(state)

    n = len(state.players)
    for bot in state.players[1:]:                  # busted bots buy back in automatically
        if bot.chips <= 0:
            bot.chips = state.starting_stack

    state.hand_number += 1
    state.action_log = []
    state.message = ""
    state.deck = new_deck()
    state.community_cards = []
    state.pot = 0
    state.acted = [False] * n
    state.contributed = [0] * n
    state.min_raise = state.big_blind
    state.raises_this_street = 0
    state.raised_flags = [False] * n
    state.preflop_aggressor = -1
    state.dealer = (state.dealer + 1) % n

    for p in state.players:
        p.hole_cards = []
        p.current_bet = 0
        p.is_folded = False
    for _ in range(2):                              # one card at a time, around the table
        for p in state.players:
            p.hole_cards.append(state.deck.pop())

    small_idx = (state.dealer + 1) % n
    big_idx = (state.dealer + 2) % n
    post_blind(state, small_idx, state.small_blind, "small blind")
    post_blind(state, big_idx, state.big_blind, "big blind")

    state.stage = "preflop"
    state.current_turn_index = big_idx
    state.current_turn_index = next_actor(state)    # first to act is left of the big blind
    return trigger_bot_turns(state)


@route
def player_action(state: State, action_choice: str, raise_amount: str, auto_muck: bool) -> Page:
    """Handle the human's fold / check-call / raise from the table page."""
    state.auto_muck = auto_muck
    human = state.players[0]
    if state.stage not in BETTING_STAGES or state.current_turn_index != 0 or not can_act(human):
        return index(state)

    state.message = ""
    to_call = max(0, highest_bet(state) - human.current_bet)

    if action_choice == "Fold":
        apply_action(state, 0, "fold")
    elif action_choice == "Raise":
        try:
            raise_by = int(raise_amount.strip())
        except ValueError:
            state.message = "Raise amount must be a whole number of chips, for example " + str(state.min_raise) + "."
            return table_page(state)

        # IF 1 - Validating Player Raise Amount
        if raise_by >= state.min_raise and to_call + raise_by <= human.chips:
            apply_action(state, 0, "raise", raise_by)
        else:
            max_raise = human.chips - to_call
            if max_raise < state.min_raise:
                state.message = ("You don't have enough chips to raise (minimum raise is "
                                 + str(state.min_raise) + "). Choose Check/Call or Fold.")
            else:
                state.message = ("Invalid raise of " + str(raise_by) + ": enter between "
                                 + str(state.min_raise) + " and " + str(max_raise) + " chips.")
            return table_page(state)
    else:
        apply_action(state, 0, "call")

    state.current_turn_index = next_actor(state)
    return trigger_bot_turns(state)


@route
def trigger_bot_turns(state: State) -> Page:
    """Run the AI opponents until it is the human's turn or the hand moves on."""
    for _ in range(300):                            # safety cap; a hand needs far fewer
        page = check_progress(state)
        if page is not None:
            return page
        idx = state.current_turn_index
        if not can_act(state.players[idx]):
            state.current_turn_index = next_actor(state)
            continue
        if idx == 0:
            return table_page(state)                # human's turn
        action, raise_by = choose_bot_action(state, idx)
        apply_action(state, idx, action, raise_by)
        state.current_turn_index = next_actor(state)
    return table_page(state)


@route
def advance_street(state: State) -> Page:
    """Move Pre-flop -> Flop -> Turn -> River -> Showdown and deal community cards."""
    cards_out = len(state.community_cards)
    if cards_out >= 5:
        return resolve_showdown(state)

    for p in state.players:                         # new street: bets reset to 0
        p.current_bet = 0
    state.acted = [False] * len(state.players)
    state.min_raise = state.big_blind
    state.raises_this_street = 0

    state.deck.pop()                                # burn a card
    if cards_out == 0:
        state.stage = "flop"
        for _ in range(3):
            state.community_cards.append(state.deck.pop())
    elif cards_out == 3:
        state.stage = "turn"
        state.community_cards.append(state.deck.pop())
    else:
        state.stage = "river"
        state.community_cards.append(state.deck.pop())

    state.current_turn_index = state.dealer         # first to act: left of the dealer
    state.current_turn_index = next_actor(state)
    return trigger_bot_turns(state)


@route
def reset_stack(state: State) -> Page:
    """Recharge the human's chip stack after going broke, then keep playing."""
    human = state.players[0]
    if human.chips <= 0:
        human.chips = state.starting_stack
        state.rebuys += 1
        state.peak_chips = max(state.peak_chips, human.chips)
        state.message = ""
        return deal_hand(state)
    return index(state)


@route
def show_rules(state: State) -> Page:
    """Navigation helper so every page can open the Rules & Hand Rankings guide."""
    return rules_page(state)


@route
def return_to_start(state: State) -> Page:
    """Leave the current game and go back to the welcome / setup screen."""
    state.stage = "setup"
    state.message = ""
    return welcome_page(state)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def welcome_page(state: State) -> Page:
    """Welcome / Setup page."""
    return Page(state, [
        Header("Texas Hold'em Poker", 1),
        "Play against four computer opponents. Set up your game below.",
        HorizontalRule(),
        "Your name:",
        TextBox("player_name", "User"),
        "Starting chip stack:",
        SelectBox("starting_stack", ["500", "1000", "2000", "5000"], "1000"),
        "Opponent difficulty:",
        SelectBox("difficulty", ["Easy", "Medium", "Hard"], "Medium"),
        "Blinds are set to 1/50 (big) and 1/100 (small) of your starting stack.",
        LineBreak(),
        Button("Start Game", start_new_game),
        Button("Rules & Hand Rankings", show_rules),
    ])


def table_page(state: State) -> Page:
    """Main poker table page."""
    human = state.players[0]
    to_call = max(0, highest_bet(state) - human.current_bet)
    max_raise = human.chips - to_call
    n = len(state.players)

    content = [Header("Hand #" + str(state.hand_number) + " - " + STAGE_LABELS.get(state.stage, state.stage), 2)]
    if state.message:
        content.append("⚠ " + state.message)
    content.append("Opponents: " + state.difficulty + "   |   Dealer, small blind and big blind rotate every hand")
    content.append(table_layout(state))

    if len(human.hole_cards) + len(state.community_cards) >= 5 and not human.is_folded:
        content.append("Your best hand right now: " + best_hand(human.hole_cards + state.community_cards)[2])

    if state.action_log:
        content.append(HorizontalRule())
        content.append("Recent action:")
        for line in state.action_log[-6:]:
            content.append("  • " + line)
    content.append(HorizontalRule())

    if state.current_turn_index == 0 and can_act(human):
        if to_call == 0:
            content.append("Your turn - nothing to call, you may check.")
        else:
            content.append("Your turn - " + str(min(to_call, human.chips)) + " to call.")
        if max_raise >= state.min_raise:
            content.append("Raise amount = chips added on top of the call. Minimum " + str(state.min_raise)
                           + ", maximum " + str(max_raise) + ".")
        else:
            content.append("You can't raise right now (not enough chips) - check/call or fold.")
        content.append("Action:")
        content.append(SelectBox("action_choice", ["Check/Call", "Raise", "Fold"], "Check/Call"))
        content.append("Raise amount (only used when Raise is selected):")
        content.append(TextBox("raise_amount", str(state.min_raise)))
        content.append("Auto-muck my losing hands at showdown:")
        content.append(CheckBox("auto_muck", state.auto_muck))
        content.append(LineBreak())
        content.append(Button("Submit Action", player_action))
    else:
        content.append(Button("Continue", trigger_bot_turns))
    content.append(Button("Rules & Hand Rankings", show_rules))
    content.append(Button("Quit to Start Screen", return_to_start))
    return Page(state, content)


def showdown_page(state: State) -> Page:
    """Showdown / Hand Results page."""
    content = [Header("Hand Results - Hand #" + str(state.hand_number), 1),
               Header(state.result_headline, 2),
               "Pot awarded: " + str(state.last_pot) + " chips",
               table_layout(state, reveal=state.reveal)]

    rows = [["Player", "Hole cards", "Hand", "Result", "Chips now"]]
    for i, p in enumerate(state.players):
        won = state.winnings[i] if i < len(state.winnings) else 0
        if p.is_folded:
            cards, hand = "Folded", "-"
        elif not state.reveal:
            cards = cards_text(p.hole_cards) if i == 0 else "Not shown"
            hand = "-"
        elif i == 0 and won == 0 and state.auto_muck:
            cards, hand = "Mucked (hidden)", "-"
        else:
            cards, hand = cards_text(p.hole_cards), state.hand_names[i]
        if won > 0:
            result = "★ WINNER  +" + str(won)
        elif state.contributed[i] > 0:
            result = "Lost " + str(state.contributed[i])
        else:
            result = "-"
        rows.append([p.name, cards, hand, result, str(p.chips)])
    content.append(Table(rows))

    if state.reveal and state.winning_cards:
        content.append("Winning 5-card hand: " + state.winning_hand_name)
        content.append(box(card_row(state.winning_cards, 62), bg=FELT, pad="8px", align="center"))
    elif not state.reveal:
        content.append("No showdown - the last player standing wins the pot.")

    content.append(HorizontalRule())
    if state.players[0].chips <= 0:
        content.append("You're out of chips.")
        content.append(Button("Continue", deal_hand))
    else:
        content.append(Button("Deal Next Hand", deal_hand))
    content.append(Button("Rules & Hand Rankings", show_rules))
    content.append(Button("Quit to Start Screen", return_to_start))
    return Page(state, content)


def game_over_page(state: State) -> Page:
    """Game Over / Rebuy page."""
    played = state.hands_played
    rate = (str(round(100 * state.hands_won / played)) + "%") if played else "n/a"
    invested = state.starting_stack * (1 + state.rebuys)
    stats = [
        ["Session statistic", "Value"],
        ["Hands played", str(played)],
        ["Hands won", str(state.hands_won)],
        ["Win rate", rate],
        ["Biggest pot won", str(state.biggest_pot)],
        ["Peak chip count", str(state.peak_chips)],
        ["Re-buys used", str(state.rebuys)],
        ["Net result", str(state.players[0].chips - invested) + " chips"],
    ]
    return Page(state, [
        Header("Game Over", 1),
        "You're out of chips. You can re-buy and keep playing, or start over.",
        Table(stats),
        HorizontalRule(),
        Button("Re-buy " + str(state.starting_stack) + " chips and keep playing", reset_stack),
        HorizontalRule(),
        Header("Or start a brand-new game", 3),
        "Your name:",
        TextBox("player_name", state.players[0].name),
        "Starting chip stack:",
        SelectBox("starting_stack", ["500", "1000", "2000", "5000"], str(state.starting_stack)),
        "Opponent difficulty:",
        SelectBox("difficulty", ["Easy", "Medium", "Hard"], state.difficulty),
        Button("Start Over", start_new_game),
        Button("Rules & Hand Rankings", show_rules),
        Button("Back to Start Screen", return_to_start),
    ])


HAND_RANKINGS = [
    ["1", "Royal Flush", "A, K, Q, J, 10, all of the same suit", "A♠ K♠ Q♠ J♠ 10♠"],
    ["2", "Straight Flush", "Five cards in sequence, all of the same suit", "9♥ 8♥ 7♥ 6♥ 5♥"],
    ["3", "Four of a Kind", "Four cards of the same rank", "Q♠ Q♥ Q♦ Q♣ 4♠"],
    ["4", "Full House", "Three of a kind plus a pair", "J♠ J♥ J♦ 8♣ 8♠"],
    ["5", "Flush", "Any five cards of the same suit", "K♦ 10♦ 8♦ 5♦ 2♦"],
    ["6", "Straight", "Five cards in sequence, mixed suits (Ace is high or low)", "9♣ 8♦ 7♠ 6♥ 5♣"],
    ["7", "Three of a Kind", "Three cards of the same rank", "7♠ 7♥ 7♦ K♣ 2♠"],
    ["8", "Two Pair", "Two different pairs", "A♠ A♦ 5♥ 5♣ 9♠"],
    ["9", "One Pair", "Two cards of the same rank", "10♠ 10♥ A♦ 7♣ 3♠"],
    ["10", "High Card", "No combination - the highest card plays", "A♠ J♦ 8♣ 5♥ 3♠"],
]


def rules_page(state: State) -> Page:
    """Rules & Hand Rankings guide."""
    return Page(state, [
        Header("Rules & Hand Rankings", 1),
        Header("How a hand works", 3),
        "1. Each player is dealt two private hole cards. The small blind and big blind post forced bets.",
        "2. Pre-flop betting: starting left of the big blind, each player may check/call, raise, or fold.",
        "3. The flop (3 community cards) is dealt, followed by another betting round.",
        "4. The turn (1 card) is dealt, followed by betting.",
        "5. The river (1 card) is dealt, followed by the final betting round.",
        "6. Showdown: remaining players reveal their cards. The best five-card hand, made from any "
        "combination of your two hole cards and the five community cards, wins the pot.",
        "If everyone else folds, the last player wins the pot immediately without showing cards.",
        Header("Betting in this game", 3),
        "Check/Call: match the current bet (or check if there is none). "
        "Raise: type how many chips to add on top of the call - at least the minimum raise shown. "
        "Fold: give up your hand. If a player is all-in for less than the others bet, "
        "a side pot is created so they can only win what they matched.",
        "Auto-muck: tick the box to hide your hand at showdown when you lose.",
        Header("Hand rankings (best to worst)", 3),
        Table([["Rank", "Hand", "Description", "Example"]]
              + [[r[0], r[1], r[2], colorize(r[3])] for r in HAND_RANKINGS]),
        "When two players have the same hand type, the higher cards win; if all five cards play "
        "equally, the pot is split.",
        LineBreak(),
        Button("Back to the Game", index),
        Button("Back to Start Screen", return_to_start),
    ])


start_server(State())