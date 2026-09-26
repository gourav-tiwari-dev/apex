"""Standing orders: what he has told the team, kept and honoured for the rest of the race.

The adaptability brick (his ask, 25 Sep: "if I said don't give me that bullshit, we will push, no
holding back, then it should act like a real person"; "it doesn't remember the decisions").

What the research said (26 Sep, see RACE_MODEL.md "Adaptability"):
  - Crew Chief keeps a driver's radio commands for the whole session ("keep quiet", "don't tell
    me the gaps", "tell me the gaps"), they are not one-off answers.
  - Playbook delegation (Miller & Parasuraman 2007, Human Factors): the human sets the play and
    its limits, the automation works inside them. Automation the human can shape beat automation
    that decides alone on awareness, acceptance and workload.
  - Verstappen, Brazil 2022: "don't ask that again to me... I gave my reasons". Once he has
    decided, the team says the cost ONCE and then respects it. It never nags.
  - Grounding (Clark & Brennan): an order is acknowledged out loud, so both sides know it stands.

So: an order is heard (code for the clear phrasings, the coach's ORDER line for the rest), said
back once, kept, shown to the coach with every question, and every seat's calls pass through it.
Only a fact that ends the race (the fuel will not make the flag) gets through a "push" order, and
it is said as his decision to make.
"""
from dataclasses import dataclass

# topic -> the stances it can take, and what he hears back
STANCES = {
    "pace": {"push": "Copy. We push, no holding back. I'll only come back on fuel if it won't make the flag.",
             "save": "Copy. We save. Lift and coast into the big stops, I'll tell you when it's enough.",
             "bring_home": "Copy. Bring it home. No risks, no lunges."},
    "fight": {"fight": "Copy. Nobody gets past without a fight.",
              "let_quick_go": "Copy. The genuinely quick ones go by clean, the rest we fight."},
    "coaching": {"off": "Copy. No more coaching, just the race.",
                 "on": "Copy. Coaching's back on."},
    "gaps": {"every_lap": "Copy. Gaps every lap.",
             "off": "Copy. No more gaps unless you ask.",
             "normal": "Copy. Gaps back to normal."},
}

# the plain ways he says them: (topic, stance, phrases). Checked in this order, first match per topic.
PHRASES = [
    ("pace", "push", ("no holding back", "flat out", "full attack", "full send", "no saving", "dont save",
                      "stop saving", "no lift and coast", "no lifting", "we push", "we will push", "were pushing",
                      "we are pushing", "im pushing", "i am pushing", "just push", "keep pushing", "pushing now")),
    ("pace", "bring_home", ("bring it home", "no risks", "no risk", "take it easy", "just finish", "play it safe")),
    ("pace", "save", ("we save", "ill save", "i will save", "saving fuel now", "start saving", "save fuel then",
                      "ok save", "okay save")),
    ("fight", "let_quick_go", ("let the quick ones go", "let the fast ones go", "let faster cars go",
                               "let the faster cars go", "let quick cars go")),
    ("fight", "fight", ("fight everything", "fight everyone", "fight them all", "nobody gets past",
                        "no one gets past", "not letting anyone", "not letting him", "not letting them",
                        "im not letting", "i wont let", "dont tell me to let", "no letting by", "we fight")),
    ("coaching", "on", ("coaching back on", "coaching on", "coach me again", "tips back on")),
    ("coaching", "off", ("no coaching", "stop coaching", "no more coaching", "i know the corners",
                         "stop telling me about corners", "no corner tips", "no more tips", "stop the tips")),
    # "off" before "every lap": "never tell me the gaps" contains "tell me the gaps"
    ("gaps", "off", ("dont tell me the gaps", "never tell me the gaps", "no gaps", "no more gaps", "stop the gaps",
                     "stop telling me the gaps")),
    ("gaps", "every_lap", ("gaps every lap", "gap every lap", "tell me the gaps", "give me the gaps",
                           "keep me updated on the gaps")),
    ("gaps", "normal", ("gaps back to normal", "normal gaps")),
]
BACK_TO_NORMAL = ("back to normal", "as you were", "cancel that", "cancel the orders", "forget what i said",
                  "your call again", "reset the orders")
# a question is not an order: "should we push?" goes to the coach
QUESTION_WORDS = ("should", "can i", "can we", "could", "do i", "do we", "shall", "what", "how", "why",
                  "is it", "are we", "will we", "worth")
# said like this, it outlives the race
FOR_GOOD = ("always", "every race", "from now on", "never", "in every race")

# the calls each order changes
COACHING_KINDS = {"CORNER_HABIT", "LAP_ONE_HABIT", "FASTEST_CAR", "CORNER_LOSS", "BALANCE"}


def plain(text):
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in text.lower().replace("'", "")).split())


def race_ending_fuel(call):
    """"box" or "short" when this fuel call means the car will not make the flag at this pace,
    None otherwise (fine, tight, saving). Those two get through a push order, once each."""
    verdict = call.facts.get("verdict")
    if verdict == "box" or (call.template or "").startswith("Box"):
        return "box"
    if verdict in ("save", "short"):
        return "short"
    return None


@dataclass
class Order:
    topic: str
    stance: str
    said: str               # his words
    lap: int
    sim_time: float
    source: str             # "him" (heard), "coach" (the coach's ORDER line), "remembered" (a past race)
    for_good: bool = False


class StandingOrders:
    def __init__(self):
        self.orders = {}           # topic -> Order
        self.decisions = []        # every order and cancel this race, oldest first, for the coach
        self.cost_said = set()     # (topic, what) costs already said once: never nag

    # ---- hearing -------------------------------------------------------------------------------
    def hear(self, text, lap, now):
        """The orders in what he said, set and acknowledged: (acknowledgement, [Order]) or None
        when it is not an order (a question, or nothing that reads as one)."""
        heard = " " + plain(text) + " "
        if "?" in text or any(" " + word + " " in heard for word in QUESTION_WORDS):
            return None
        if any(" " + words + " " in heard for words in BACK_TO_NORMAL):
            self.clear(text, lap, now)
            return "Copy. Back to normal, my calls again.", []
        found = []
        topics = set()
        for topic, stance, phrases in PHRASES:
            if topic in topics:
                continue
            if any(" " + phrase + " " in heard for phrase in phrases):
                topics.add(topic)
                found.append(self.set(topic, stance, text, lap, now, "him",
                                      for_good=any(" " + w + " " in heard for w in FOR_GOOD)))
        if not found:
            return None
        return " ".join(STANCES[o.topic][o.stance] for o in found), found

    def set(self, topic, stance, said, lap, now, source, for_good=False):
        if stance not in STANCES.get(topic, {}):
            raise ValueError(f"no such order: {topic}={stance}")
        order = Order(topic, stance, said, lap, now, source, for_good)
        # "coaching on" and "gaps normal" are the defaults: they clear the order
        if (topic, stance) in (("coaching", "on"), ("gaps", "normal")):
            self.orders.pop(topic, None)
        else:
            self.orders[topic] = order
        self.cost_said = {c for c in self.cost_said if c[0] != topic}
        self.decisions.append(order)
        return order

    def clear(self, said, lap, now):
        self.orders = {}
        self.cost_said = set()
        self.decisions.append(Order("all", "back_to_normal", said, lap, now, "him"))

    def get(self, topic):
        order = self.orders.get(topic)
        return order.stance if order else None

    # ---- the radio -----------------------------------------------------------------------------
    def adjust(self, call):
        """The call as his orders allow it: None when an order says not to say it, the call
        (maybe reworded) otherwise. Safety and race control are never touched."""
        if call.asked:
            return call                      # what he asked for, he gets
        verdict = race_ending_fuel(call) if call.kind == "FUEL" else None
        if verdict is not None and self.get("pace") == "push":
            # what his order was said back with: "I'll only come back on fuel if it won't make
            # the flag" (replay of 25 Sep 12:31: "short by 0.1 laps" was silenced under push)
            key = ("pace", verdict)
            if key in self.cost_said:
                return None                  # said once; his call now
            self.cost_said.add(key)
            short = call.facts.get("spare_laps")
            if verdict == "box":
                words = (f"You said push, your call. Straight: it won't make the flag, short by {short} laps. "
                         "Box this lap or you stop.")
            else:
                words = (f"You said push, your call. Straight: at this pace it won't make the flag, short by "
                         f"{short} laps. Lift and coast in the big stops, or box.")
            call.template = call.conclusion = words
            return call
        if call.kind == "FIGHT_COST" and self.get("fight") == "fight":
            # he said fight: the call keeps its facts but loses the "settle" option
            words = (call.template or "").replace(" Commit or settle.", " Commit.").replace(" this lap or settle in.", " this lap.")
            call.template = call.conclusion = words
        if self.forbids(call):
            return None
        return call

    def forbids(self, call):
        """True when an order says this call must not go out. Changes nothing, so the Governor can
        ask again about calls already waiting when an order arrives (replay of 23 Sep, 26 Sep:
        "Lap 1: survive it" was queued in the same frame as "no more coaching" and still went out)."""
        if call.asked:
            return False
        pace = self.get("pace")
        words = call.template or ""
        if call.kind == "FUEL" and pace == "push":
            # fine / tight / saving: he said push. Short or box gets through: they end the race
            if words.startswith("You said push"):
                return False
            return race_ending_fuel(call) is None
        if call.kind in COACHING_KINDS and self.get("coaching") == "off":
            return True
        if call.kind == "GAP_REPORT" and self.get("gaps") == "off":
            return True
        if call.kind == "ATTACK_PLAN" and pace == "bring_home":
            return True
        if call.kind == "FIGHT_COST" and self.get("fight") == "fight":
            # still offers "settle": it was worded before he said fight
            return "settle" in words.lower()
        return False

    def gaps_every_lap(self):
        return self.get("gaps") == "every_lap"

    # ---- the coach -----------------------------------------------------------------------------
    def for_coach(self):
        """His orders and decisions this race, in words, for every question the coach answers."""
        lines = []
        for order in self.orders.values():
            lines.append(f"{order.topic} = {order.stance} (lap {order.lap}, he said: \"{order.said}\")")
        history = [f"lap {o.lap}: {o.topic}={o.stance}" for o in self.decisions[-6:]]
        return {"standing_orders": lines or ["none: your calls stand"], "his_decisions_this_race": history}

    # ---- between races -------------------------------------------------------------------------
    def save(self, conn):
        """The orders he said for good ("never tell me the gaps") outlive the race."""
        conn.execute("CREATE TABLE IF NOT EXISTS standing_orders (topic TEXT PRIMARY KEY, stance TEXT, said TEXT)")
        for order in self.decisions:
            if order.for_good and order.topic in STANCES:
                if (order.topic, order.stance) in (("coaching", "on"), ("gaps", "normal")):
                    conn.execute("DELETE FROM standing_orders WHERE topic = ?", (order.topic,))
                else:
                    conn.execute("INSERT OR REPLACE INTO standing_orders VALUES (?, ?, ?)",
                                 (order.topic, order.stance, order.said))
        conn.commit()

    def load(self, conn):
        conn.execute("CREATE TABLE IF NOT EXISTS standing_orders (topic TEXT PRIMARY KEY, stance TEXT, said TEXT)")
        for topic, stance, said in conn.execute("SELECT topic, stance, said FROM standing_orders"):
            if stance in STANCES.get(topic, {}):
                self.orders[topic] = Order(topic, stance, said, 0, 0.0, "remembered", True)


def current_plan(orders, fuel_now, damaged, laps_to_go):
    """The plan right now, rebuilt from the live facts and his orders every time it is asked, so
    it changes when the race does (fuel turns, damage, the last lap) and never contradicts him.
    The spoken changes already come from the seats (FUEL, DAMAGE, LAST_LAP); this is what the coach
    answers "what's the plan?" with, so the answer matches them."""
    pace = orders.get("pace") if orders is not None else None
    fight = orders.get("fight") if orders is not None else None
    verdict = fuel_now.get("verdict") if isinstance(fuel_now, dict) else None     # "not known yet" is text
    parts = []
    if verdict == "box":
        parts.append("box this lap for fuel: it will not make the flag (the one fact that beats a push order)")
    elif pace == "push":
        parts.append("push to the flag, no saving (his order)")
    elif pace == "bring_home":
        parts.append("bring it home: no risks, no lunges (his order)")
    elif pace == "save" or verdict in ("save", "saving"):
        parts.append("save fuel: lift and coast into the big stops")
    elif verdict == "tight":
        parts.append("fuel is tight: a little lift and coast into the big stops")
    else:
        parts.append("race: fuel is fine, push")
    if fight == "fight":
        parts.append("fight every place (his order)")
    elif fight == "let_quick_go":
        parts.append("the genuinely quick cars go by clean, fight the rest (his order)")
    if damaged:
        parts.append("the car has damage: watch the lap times")
    if laps_to_go is not None and laps_to_go <= 1:
        parts.append("last lap: every place counts")
    return "; ".join(parts)
