"""Question-test driver for a Mind free-question route (fable-5, draft 7, 2026-11-22).

DRAFT for the second Mind change. This file holds NO question and NO answer: the question sets it
reads are private evaluation material and are never committed, posted or indexed.

BASE. Written against main a30bbb625c3fa7c382524e7e51f94efb887e01fe as read through git show:
tools/wd_mind_full_runtime.py (blob sha256 22a86ff4..., map_pipe_candidates at line 3970),
tools/wd_bridge_mind_session.py (9238ff35..., request built at line 1623, scope per lane at line 1292),
tools/wd_mind_content_query_readonly.py (cd5a907e..., 64-hex rule at line 704, lanes at line 624).
That tree has NO free-question route: the pipe accepts only query id startup_work_overview.

PROPOSED V3 (Lead 15:28Z 11-22; my assumed shape until the Tools fixture is seen, one function to change):
request  = the five existing keys with schema wd.mind-pipe-request.v3 and query_id free_question,
           plus the key question (text);
response = the non-location mapper answer (Lead 15:34Z and 15:41Z): schema wd.mind-pipe-candidates.v1 exactly,
           nonce, lane and scope echoed, status CANDIDATES or ABSTAIN, at most 3 candidates, each with exactly
           ref_sha256, doc_id_sha256, source_event_sha256. The v3 label belongs to the request only.

SCOPE RULE. The driver asks with the scope the product gives each lane. At this revision that is the
shared scope for all five lanes (PRODUCT_SCOPE, equal to the two product helpers), so every gold row is in
reach of every lane. The owner and the resolver still honour an own scope when one is asked, and the scorer
keeps its own-scope classes for that: with scope own a lane is served only rows it wrote itself, a gold row
written by another lane is OUT OF REACH and is counted apart from a miss, and a served row that any loaded
set knows to be written by another lane is classed SERVED_OUTSIDE_SCOPE before any hit is awarded, also
when the answer mixes own and foreign rows. EARLIER_SCOPE (the table of the base a30bbb62, build lanes own)
is kept only for the self test of those classes. A served row whose author no loaded set knows cannot be
judged here: the scope filter itself needs its own product tests.

ADMISSION RULE. Mind admits rows written by the build lanes only (BUILD_LANES; _BUILD_LANES at line 625 of
tools/wd_mind_content_query_readonly.py at the base). A gold row written by any other lane can never be
served: it is not reachable gold for any lane under any scope, and an answerable question whose gold holds
no admitted row is OUT OF REACH for everyone and is counted (answerable_asks_without_admitted_gold). A
served row that a loaded set knows to be written by a lane outside BUILD_LANES is classed
SERVED_OUTSIDE_SCOPE under every scope.

PRIVATE OUTPUT. convert-v1 and run take the private evaluation root as an argument (--private-root): an
existing folder whose own name on disk is .codex-audit (ignored by Git) and that does not lie below a bridge
folder. Output is written only strictly inside that root, judged after links are resolved, and never into a
folder named .agent-bridge. Names are compared the Windows way: letter case and trailing dots or spaces do
not make another folder. Any other target is refused before anything is written.

ROUTES. A route is called as route(request, caller): caller is the trusted (lane, scope) of the asking
lane, handed over by the driver beside the request and never read out of the request object; the draft
mapper takes it as free_question_binding and refuses a request whose lane or scope differs from it.
The synthetic stand-in is NOT Mind: a word-overlap toy over six invented rows, used to exercise the driver.
MapperRoute binds an in-process mapper function that the caller hands in (this file imports no product
code); the draft binding test uses it over a synthetic owner layer. The command-line route pipe-v3 still
refuses: a run against a real owner needs Lead's yes with an exact commit.

HANDLING. Question texts and gold rows are evaluation material: they stay in private files on this machine.
This driver prints and saves cards (counts), ids, classes and hashes only, never a question text.

MODES (run with the fleet-pinned Python 3.13, flags -I -B):
  selftest                         synthetic sets through the synthetic route; must print SELFTEST MATCH
  check SET.json [...]             validate private question set files; prints counts and file hashes
  convert-v1 --private-root ROOT V1.json OUT.json
                                   my usefulness test set v1 -> this driver's set format (stays private)
  run --route synthetic --lanes a,b --private-root ROOT --out DIR SET.json [...]
"""
import contextlib
import hashlib
import json
import re
import secrets
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

BASE_COMMIT = 'a30bbb625c3fa7c382524e7e51f94efb887e01fe'
LANES = ('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2')
BUILD_LANES = ('codex-lead-1', 'codex-tools-1', 'fable-5')  # the only lanes whose rows Mind admits
PRIVATE_FOLDER = '.codex-audit'
BRIDGE_FOLDER = '.agent-bridge'
# The scope each lane asks with. PRODUCT_SCOPE mirrors the two product helpers of this revision.
PRODUCT_SCOPE = {lane: 'shared' for lane in LANES}
# The table of the base a30bbb62. Kept only so that the self test still exercises the own-scope classes.
EARLIER_SCOPE = {'codex-lead-1': 'own', 'codex-tools-1': 'own', 'fable-5': 'own',
                 'claude-rco-1': 'shared', 'claude-rco-2': 'shared'}
SCOPE = dict(PRODUCT_SCOPE)  # the active table; real runs never change it
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
SET_SCHEMA = 'fable5.mind-question-set.v1'
GOLD_KIND = 'sha256_of_canonical_event_json'
REQUEST_SCHEMA = 'wd.mind-pipe-request.v3'
QUERY_ID = 'free_question'
RESPONSE_SCHEMA = 'wd.mind-pipe-candidates.v1'
CANDIDATE_KEYS = ('ref_sha256', 'doc_id_sha256', 'source_event_sha256')
MAX_CANDIDATES = 3
MAX_QUESTION_CHARS = 512
CLASSES = ('HIT_FIRST', 'HIT_WITHIN_THREE', 'MISS_WITH_CANDIDATES', 'MISS_NO_CANDIDATES',
           'OUT_OF_REACH_NO_CANDIDATES', 'OUT_OF_REACH_WITH_CANDIDATES',
           'NEGATIVE_PASS', 'NEGATIVE_FALSE_CANDIDATES', 'SERVED_OUTSIDE_SCOPE', 'REFUSED')
JUDGED = ('HIT_FIRST', 'HIT_WITHIN_THREE', 'MISS_WITH_CANDIDATES', 'MISS_NO_CANDIDATES')


class DriverError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def is_hex(value):
    return type(value) is str and HEX64.fullmatch(value) is not None


def is_text(value):
    """False for text that cannot be written as UTF-8, such as a lone surrogate."""
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def same_folder_name(part, name):
    """Windows path comparison: letter case and trailing dots or spaces do not make another folder."""
    return part.rstrip(' .').casefold() == name.casefold()


def private_root(root):
    """The declared private evaluation root: an existing folder whose own name on disk is .codex-audit
    and that does not lie below a bridge folder."""
    if not isinstance(root, (str, Path)) or not str(root):
        raise DriverError('private_root')
    resolved = Path(root).resolve()
    if (not resolved.is_dir() or resolved.name != PRIVATE_FOLDER
            or any(same_folder_name(part, BRIDGE_FOLDER) for part in resolved.parts)):
        raise DriverError('private_root')
    return resolved


def private_target(path, root):
    """Evaluation output goes only strictly inside the declared private root, judged after links are
    resolved, and never into a bridge folder."""
    base = private_root(root)
    if not isinstance(path, (str, Path)) or not str(path):
        raise DriverError('output_not_private')
    resolved = Path(path).resolve()
    try:
        inside = resolved.relative_to(base).parts
    except ValueError:
        raise DriverError('output_not_private') from None
    if not inside or any(same_folder_name(part, BRIDGE_FOLDER) for part in inside):
        raise DriverError('output_not_private')
    return resolved


# ---------------------------------------------------------------- request and response (proposed v3)

@contextlib.contextmanager
def using_scope(table):
    """Swap the active lane table for one block. For the self test and tests only."""
    if (type(table) is not dict or set(table) != set(LANES)
            or any(value not in ('own', 'shared') for value in table.values())):
        raise DriverError('scope_table')
    saved = dict(SCOPE)
    SCOPE.clear()
    SCOPE.update(table)
    try:
        yield
    finally:
        SCOPE.clear()
        SCOPE.update(saved)


def build_request(question, lane, scope):
    """The ONE place that knows the proposed v3 request shape."""
    return {'schema': REQUEST_SCHEMA, 'query_id': QUERY_ID, 'nonce': secrets.token_hex(32),
            'lane': lane, 'scope': scope, 'question': question}


def request_shape():
    sample = build_request('x', LANES[0], SCOPE[LANES[0]])
    return {'keys': sorted(sample), 'schema': sample['schema'], 'query_id': sample['query_id']}


def validate_response(response, request):
    """Return (status, [source_event_sha256 in order]) or raise DriverError(reason). Nothing is assumed:
    a response that cannot be read as the agreed shape is a refusal, never an empty answer."""
    if type(response) is not dict:
        raise DriverError('response_not_object')
    if set(response) != {'schema', 'nonce', 'lane', 'scope', 'status', 'candidates'}:
        raise DriverError('response_keys')
    if response['schema'] != RESPONSE_SCHEMA:
        raise DriverError('response_schema')
    if response['nonce'] != request['nonce']:
        raise DriverError('nonce_mismatch')
    if response['lane'] != request['lane'] or response['scope'] != request['scope']:
        raise DriverError('lane_or_scope_mismatch')
    rows = response['candidates']
    if response['status'] not in ('CANDIDATES', 'ABSTAIN') or type(rows) is not list:
        raise DriverError('status')
    if (response['status'] == 'CANDIDATES') != bool(rows) or len(rows) > MAX_CANDIDATES:
        raise DriverError('candidate_count')
    seen, order = set(), []
    for row in rows:
        if type(row) is not dict or set(row) != set(CANDIDATE_KEYS):
            raise DriverError('candidate_keys')
        if any(not is_hex(row[key]) for key in CANDIDATE_KEYS):
            raise DriverError('candidate_hash')
        key = tuple(row[name] for name in CANDIDATE_KEYS)
        if key in seen:
            raise DriverError('candidate_duplicate')
        seen.add(key)
        order.append(row['source_event_sha256'])
    return response['status'], order


# ---------------------------------------------------------------- routes

class SyntheticOwner:
    """A toy stand-in, NOT Mind: share of the question's words found in a row, six invented rows.
    One row is written by a reviewer lane and is never served, as Mind admits rows of build lanes only."""
    name = 'synthetic'
    THRESHOLD = 0.5
    ROWS = (('codex-lead-1', 'the synthetic amber valve was replaced on tuesday'),
            ('codex-lead-1', 'synthetic amber valve inspection is planned for friday'),
            ('codex-tools-1', 'the synthetic cobalt ledger moved to the north shelf'),
            ('claude-rco-1', 'synthetic review of the cobalt ledger found two gaps'),
            ('fable-5', 'the synthetic jade counter reads forty units'),
            ('codex-lead-1', 'synthetic garnet pump runs at low speed'))

    @staticmethod
    def row_hash(index):
        return sha(('synthetic-row-%d' % (index + 1)).encode())

    @staticmethod
    def words(text):
        return set(re.findall(r'[a-z0-9]+', text.lower()))

    def __call__(self, request, caller=None):
        if (type(request) is not dict
                or set(request) != {'schema', 'query_id', 'nonce', 'lane', 'scope', 'question'}
                or any(type(value) is not str for value in request.values())
                or request['schema'] != REQUEST_SCHEMA or request['query_id'] != QUERY_ID
                or not is_hex(request['nonce']) or request['lane'] not in LANES
                or request['scope'] != SCOPE[request['lane']]
                or not 1 <= len(request['question']) <= MAX_QUESTION_CHARS
                or any(ord(char) < 32 for char in request['question'])
                or not is_text(request['question'])):
            raise DriverError('synthetic_owner_refused')
        # Mirrors the draft mapper: a trusted caller identity must equal the lane and scope of the request.
        if caller is not None and (type(caller) is not tuple or caller != (request['lane'], request['scope'])):
            raise DriverError('synthetic_owner_refused')
        asked = self.words(request['question'])
        if not asked:
            raise DriverError('synthetic_owner_refused')
        scored = []
        for index, (agent, text) in enumerate(self.ROWS):
            if agent not in BUILD_LANES or (request['scope'] == 'own' and agent != request['lane']):
                continue
            score = len(asked & self.words(text)) / len(asked)
            if score >= self.THRESHOLD:
                scored.append((-score, index))
        rows = [{'ref_sha256': sha(b'ref-%d' % index), 'doc_id_sha256': sha(b'doc-%d' % index),
                 'source_event_sha256': self.row_hash(index)} for _, index in sorted(scored)[:MAX_CANDIDATES]]
        return {'schema': RESPONSE_SCHEMA, 'nonce': request['nonce'], 'lane': request['lane'],
                'scope': request['scope'], 'status': 'CANDIDATES' if rows else 'ABSTAIN', 'candidates': rows}


class PipeV3NotBound:
    name = 'pipe-v3'

    def __call__(self, request, caller=None):
        raise DriverError('route_not_bound: no free-question route exists at %s; bind to an exact commit '
                          'on the yes of Lead before any real ask' % BASE_COMMIT[:8])


class MapperRoute:
    """Binds an in-process mapper function to the driver.

    The mapper is called as mapper(enabled=True, handler=handler, request=request,
    free_question_binding=caller): the trusted caller identity goes in beside the request, unchanged.
    The caller of this class hands in the mapper, its handler and the mapper's refusal class, so the
    driver itself imports no product code. A refusal of the mapper is one counted refusal of the ask.
    """
    name = 'in-process-mapper'

    def __init__(self, mapper, handler, refusal):
        if not callable(mapper) or not (isinstance(refusal, type) and issubclass(refusal, Exception)):
            raise DriverError('mapper_route_arguments')
        self._mapper, self._handler, self._refusal = mapper, handler, refusal

    def __call__(self, request, caller=None):
        try:
            return self._mapper(enabled=True, handler=self._handler, request=request,
                                free_question_binding=caller)
        except self._refusal:
            raise DriverError('mapper_refused') from None


ROUTES = {'synthetic': SyntheticOwner, 'pipe-v3': PipeV3NotBound}


# ---------------------------------------------------------------- question sets

def load_set(path):
    data = Path(path).read_bytes()
    try:
        document = json.loads(data.decode('utf-8-sig'))
    except Exception:
        raise DriverError('set_not_json') from None
    return check_set(document), sha(data)


def check_set(document):
    if type(document) is not dict or document.get('schema') != SET_SCHEMA:
        raise DriverError('set_schema')
    if document.get('gold_kind') != GOLD_KIND:
        raise DriverError('set_gold_kind')
    for key in ('set_id', 'author'):
        if type(document.get(key)) is not str or not document[key]:
            raise DriverError('set_' + key)
    questions = document.get('questions')
    if type(questions) is not list or not questions:
        raise DriverError('set_questions')
    seen, authors = set(), {}
    for item in questions:
        if type(item) is not dict:
            raise DriverError('question_not_object')
        ident = item.get('id')
        if type(ident) is not str or not ident or ident in seen:
            raise DriverError('question_id')
        seen.add(ident)
        text = item.get('question')
        if type(text) is not str or not text.strip() or not is_text(text):
            raise DriverError('question_text:' + ident)
        if item.get('expect') not in ('ANSWER', 'ABSTAIN'):
            raise DriverError('question_expect:' + ident)
        if item.get('language') not in ('en', 'fi'):
            raise DriverError('question_language:' + ident)
        if type(item.get('category')) is not str or not item['category']:
            raise DriverError('question_category:' + ident)
        gold = item.get('gold')
        if type(gold) is not list or (item['expect'] == 'ANSWER') != bool(gold):
            raise DriverError('question_gold:' + ident)
        for row in gold:
            if (type(row) is not dict or not is_hex(row.get('source_event_sha256'))
                    or type(row.get('agent')) is not str or not row['agent']):
                raise DriverError('question_gold_row:' + ident)
            if authors.setdefault(row['source_event_sha256'], row['agent']) != row['agent']:
                raise DriverError('gold_author_conflict:' + ident)
        ask_as = item.get('ask_as')
        if ask_as != 'any' and (type(ask_as) is not list or not ask_as or len(set(ask_as)) != len(ask_as)
                                or any(lane not in LANES for lane in ask_as)):
            raise DriverError('question_ask_as:' + ident)
    return document


def convert_v1(source, target, root):
    """My usefulness test set v1 (2026-11-14) into this driver's format; both language forms of a question
    become two entries. The output is evaluation material and stays private."""
    target = private_target(target, root)
    old = json.loads(Path(source).read_bytes().decode('utf-8-sig'))
    if old.get('event_id_kind') != GOLD_KIND:
        raise DriverError('v1_event_id_kind')
    questions = []
    for item in old['questions']:
        gold = [{'source_event_sha256': row['event_id'], 'agent': row['agent']} for row in item['gold']]
        for language in ('en', 'fi'):
            questions.append({
                'id': '%s.%s' % (item['id'], language), 'language': language, 'question': item['q_' + language],
                'expect': item['expect'], 'category': item['category'], 'split': item['split'],
                'gold': gold if item['expect'] == 'ANSWER' else [],
                'ask_as': 'any' if item['lane'] == 'any' else [item['lane']]})
    document = {'schema': SET_SCHEMA, 'set_id': 'fable5-usefulness-v1-20261114', 'author': 'fable-5',
                'gold_kind': GOLD_KIND, 'as_of_prefix_sha256': old['as_of']['log_prefix_sha256'],
                'handling': 'evaluation material; private; never on the bridge, in a PR or in a Mind source',
                'questions': questions}
    check_set(document)
    data = json.dumps(document, ensure_ascii=False, indent=1).encode('utf-8')
    Path(target).write_bytes(data)
    return document, sha(data)


# ---------------------------------------------------------------- one ask and the card

def gold_authors(sets):
    """Every gold reference of the loaded sets with the lane that wrote it; one author per reference."""
    authors = {}
    for document, _ in sets:
        for item in document['questions']:
            for row in item['gold']:
                if authors.setdefault(row['source_event_sha256'], row['agent']) != row['agent']:
                    raise DriverError('gold_author_conflict')
    return authors


def ask(route, item, lane, authors=None):
    """One question asked as one lane. Returns (class, reason, seconds, candidate count).

    authors: the gold references of all loaded sets with their writers. With scope own only the gold rows
    the asking lane wrote can be a hit, and every reference known to be written by another lane is foreign.
    """
    scope = SCOPE[lane]
    known = dict(authors or {})
    for row in item['gold']:
        known.setdefault(row['source_event_sha256'], row['agent'])
    # Mind admits rows of build lanes only: other gold can never be served, and a served row known to
    # be written by a lane outside the build lanes is outside what any lane may be given.
    admitted = [row for row in item['gold'] if row['agent'] in BUILD_LANES]
    foreign = {reference for reference, agent in known.items() if agent not in BUILD_LANES}
    if scope == 'own':
        gold = {row['source_event_sha256'] for row in admitted if row['agent'] == lane}
        foreign |= {reference for reference, agent in known.items() if agent != lane}
    else:
        gold = {row['source_event_sha256'] for row in admitted}
    request = build_request(item['question'], lane, scope)
    frozen = dict(request)
    started = time.perf_counter()
    try:
        # The second argument is the trusted caller identity: the driver's own lane and scope, handed
        # over beside the request and never read back out of the request object.
        response = route(request, (lane, scope))
        elapsed = time.perf_counter() - started
        if request != frozen:
            raise DriverError('request_changed_by_route')
        status, order = validate_response(response, frozen)
    except DriverError as error:
        return 'REFUSED', str(error).split(':')[0], time.perf_counter() - started, None
    except Exception as error:  # an unknown failure is a refusal with its type, never an empty answer
        return 'REFUSED', 'route_raised_' + type(error).__name__, time.perf_counter() - started, None
    # Checked before any hit is awarded and for every kind of question: a row known to be written by
    # another lane must never be served under scope own, alone or mixed with own rows.
    if foreign & set(order):
        return 'SERVED_OUTSIDE_SCOPE', '', elapsed, len(order)
    if item['expect'] == 'ABSTAIN':
        return ('NEGATIVE_PASS' if status == 'ABSTAIN' else 'NEGATIVE_FALSE_CANDIDATES'), '', elapsed, len(order)
    if not gold:
        return ('OUT_OF_REACH_WITH_CANDIDATES' if order else 'OUT_OF_REACH_NO_CANDIDATES'), '', elapsed, len(order)
    if not order:
        return 'MISS_NO_CANDIDATES', '', elapsed, 0
    if order[0] in gold:
        return 'HIT_FIRST', '', elapsed, len(order)
    return ('HIT_WITHIN_THREE' if gold & set(order) else 'MISS_WITH_CANDIDATES'), '', elapsed, len(order)


def rate(part, whole):
    return None if not whole else round(part / whole, 4)


def in_reach(item, lane):
    """None for a negative control; else whether the asking lane can be served a gold row at all."""
    if item['expect'] == 'ABSTAIN':
        return None
    admitted = [row['agent'] for row in item['gold'] if row['agent'] in BUILD_LANES]
    return bool(admitted) if SCOPE[lane] == 'shared' else lane in admitted


def gold_admitted(item):
    """None for a negative control; else whether any gold row was written by a lane Mind admits."""
    if item['expect'] == 'ABSTAIN':
        return None
    return any(row['agent'] in BUILD_LANES for row in item['gold'])


def summarize(rows):
    """Rates are over ALL answerable asks in reach and ALL negative asks: a refusal or a row served
    outside scope lowers them, it never drops out of the count."""
    counts = Counter(row['class'] for row in rows)
    reach = sum(1 for row in rows if row['in_reach'])
    negatives = sum(1 for row in rows if row['expect'] == 'ABSTAIN')
    return {'asks': len(rows), 'classes': {name: counts[name] for name in CLASSES},
            'answerable_asks_in_reach': reach,
            'answerable_asks_without_admitted_gold': sum(1 for row in rows if row.get('gold_admitted') is False),
            'in_reach_and_answered_by_route': sum(counts[name] for name in JUDGED),
            'hit_first_rate': rate(counts['HIT_FIRST'], reach),
            'hit_within_three_rate': rate(counts['HIT_FIRST'] + counts['HIT_WITHIN_THREE'], reach),
            'negative_controls': negatives, 'negative_pass_rate': rate(counts['NEGATIVE_PASS'], negatives)}


def run(route, sets, lanes, mine='fable-5'):
    rows, authors, not_asked = [], gold_authors(sets), 0
    for document, digest in sets:
        for item in document['questions']:
            asking = lanes if item['ask_as'] == 'any' else [name for name in item['ask_as'] if name in lanes]
            not_asked += not asking
            for lane in asking:
                kind, reason, seconds, served = ask(route, item, lane, authors)
                rows.append({'set': document['set_id'], 'author': document['author'], 'id': item['id'],
                             'language': item['language'], 'category': item['category'], 'lane': lane,
                             'scope': SCOPE[lane], 'expect': item['expect'], 'in_reach': in_reach(item, lane),
                             'gold_admitted': gold_admitted(item),
                             'class': kind, 'reason': reason, 'candidates': served,
                             'milliseconds': round(seconds * 1000, 3)})
    if not rows:
        raise DriverError('no_asks')

    def split(key):
        return {value: summarize([row for row in rows if row[key] == value])
                for value in sorted({row[key] for row in rows})}

    def part(own):
        chosen = [row for row in rows if (row['author'] == mine) == own]
        return summarize(chosen) if chosen else None

    times = sorted(row['milliseconds'] for row in rows)
    card = {'schema': 'fable5.mind-question-test-card.v1', 'route': route.name,
            'label': ('SYNTHETIC STAND-IN, NOT A MIND MEASUREMENT' if route.name == 'synthetic'
                      else 'route %s' % route.name),
            'base_commit': BASE_COMMIT, 'driver_sha256': sha(Path(__file__).read_bytes()),
            'request_shape': request_shape(), 'lanes_asked': list(lanes),
            'questions_not_asked_because_of_the_lane_choice': not_asked,
            'sets': [{'set_id': document['set_id'], 'author': document['author'], 'file_sha256': digest,
                      'questions': len(document['questions'])} for document, digest in sets],
            'all': summarize(rows), 'questions_by_others': part(False), 'questions_by_me': part(True),
            'by_lane': split('lane'), 'by_language': split('language'), 'by_category': split('category'),
            'refusal_reasons': dict(Counter(row['reason'] for row in rows if row['class'] == 'REFUSED')),
            'milliseconds': {'median': statistics.median(times), 'max': times[-1],
                             'ninth_decile': times[min(len(times) - 1, int(len(times) * 0.9))]}}
    return card, rows


# ---------------------------------------------------------------- self test

def synthetic_sets():
    def gold(index):
        return [{'source_event_sha256': SyntheticOwner.row_hash(index), 'agent': SyntheticOwner.ROWS[index][0]}]

    def question(ident, text, lane, gold_rows, expect='ANSWER', language='en', category='synthetic'):
        return {'id': ident, 'language': language, 'question': text, 'expect': expect, 'category': category,
                'gold': gold_rows, 'ask_as': [lane]}

    mine = {'schema': SET_SCHEMA, 'set_id': 'synthetic-a', 'author': 'fable-5', 'gold_kind': GOLD_KIND,
            'questions': [
                question('A1', 'when was the synthetic amber valve replaced', 'codex-lead-1', gold(0)),
                question('A2', 'synthetic amber valve inspection', 'codex-lead-1', gold(0)),
                question('A3', 'synthetic garnet pump speed on friday', 'codex-lead-1', gold(0)),
                question('A4', 'which shelf holds the quartz binder', 'codex-lead-1', gold(0)),
                question('A5', 'where did the synthetic cobalt ledger move', 'codex-lead-1', gold(2)),
                question('A6', 'synthetic cobalt ledger gaps found', 'codex-tools-1', gold(3))]}
    others = {'schema': SET_SCHEMA, 'set_id': 'synthetic-b', 'author': 'codex-lead-1', 'gold_kind': GOLD_KIND,
              'questions': [
                  question('B1', 'where did the synthetic cobalt ledger move', 'claude-rco-1', gold(2)),
                  question('B2', 'synthetic jade counter reads', 'claude-rco-2', gold(4)),
                  question('B3', 'what colour is the opal lantern', 'claude-rco-1', [], expect='ABSTAIN',
                           category='negative'),
                  question('B4', 'synthetic amber valve', 'claude-rco-2', [], expect='ABSTAIN', category='negative'),
                  question('B5', 'synthetic jade\x00counter', 'fable-5', gold(4)),
                  question('B6', 'paljonko synteettinen jade counter reads', 'fable-5', gold(4), language='fi')]}
    return [(check_set(mine), sha(b'synthetic-a')), (check_set(others), sha(b'synthetic-b'))]


# Worked out by hand from the six invented rows and the word-share rule before the first run.
EXPECTED_SELFTEST = {
    'per_ask': {'A1': 'HIT_FIRST', 'A2': 'HIT_WITHIN_THREE', 'A3': 'MISS_WITH_CANDIDATES',
                'A4': 'MISS_NO_CANDIDATES', 'A5': 'OUT_OF_REACH_NO_CANDIDATES',
                'A6': 'OUT_OF_REACH_WITH_CANDIDATES', 'B1': 'HIT_FIRST', 'B2': 'HIT_FIRST',
                'B3': 'NEGATIVE_PASS', 'B4': 'NEGATIVE_FALSE_CANDIDATES', 'B5': 'REFUSED', 'B6': 'HIT_FIRST'},
    # B5 is in reach and refused by the stand-in: it stays in the count that the rates are taken over.
    'all': {'asks': 12, 'answerable_asks_in_reach': 8, 'in_reach_and_answered_by_route': 7,
            'hit_first_rate': 0.5, 'hit_within_three_rate': 0.625, 'negative_controls': 2,
            'negative_pass_rate': 0.5},
    'mine': {'asks': 6, 'answerable_asks_in_reach': 4, 'in_reach_and_answered_by_route': 4,
             'hit_first_rate': 0.25, 'hit_within_three_rate': 0.5, 'negative_controls': 0},
    'others': {'asks': 6, 'answerable_asks_in_reach': 4, 'in_reach_and_answered_by_route': 3,
               'hit_first_rate': 0.75, 'hit_within_three_rate': 0.75, 'negative_controls': 2},
    'refusal_reasons': {'synthetic_owner_refused': 1}}

# The same twelve asks under PRODUCT_SCOPE, worked out by hand before the run. A5 asks a build lane for a
# row another build lane wrote: out of reach under the earlier table, a first hit under shared visibility.
# A6 asks for a row a reviewer lane wrote: Mind admits no such row, so it stays out of reach for everyone
# (draft 5 wrongly worked it out as a first hit; RCO2 named the error on 2026-11-22).
EXPECTED_PRODUCT = {
    'per_ask': dict(EXPECTED_SELFTEST['per_ask'], A5='HIT_FIRST'),
    'all': {'asks': 12, 'answerable_asks_in_reach': 9, 'answerable_asks_without_admitted_gold': 1,
            'in_reach_and_answered_by_route': 8, 'hit_first_rate': 0.5556, 'hit_within_three_rate': 0.6667,
            'negative_controls': 2, 'negative_pass_rate': 0.5},
    'mine': {'asks': 6, 'answerable_asks_in_reach': 5, 'answerable_asks_without_admitted_gold': 1,
             'in_reach_and_answered_by_route': 5, 'hit_first_rate': 0.4, 'hit_within_three_rate': 0.6,
             'negative_controls': 0},
    'others': EXPECTED_SELFTEST['others'],
    'refusal_reasons': {'synthetic_owner_refused': 1}}


class _OwnerIgnoringScope(SyntheticOwner):
    """Fault stand-in: serves a build lane rows written by others."""

    def __call__(self, request, caller=None):
        reply = SyntheticOwner.__call__(self, dict(request, lane='claude-rco-1', scope='shared'))
        return dict(reply, lane=request['lane'], scope=request['scope'])


class _OwnerChangingRequest(SyntheticOwner):
    """Fault stand-in: alters the caller's request object."""

    def __call__(self, request, caller=None):
        reply = SyntheticOwner.__call__(self, request, caller)
        request['question'] = 'x'
        return reply


class _RouteGivingAnotherCaller(SyntheticOwner):
    """Fault stand-in: the caller identity handed to the owner is not the lane of the request."""

    def __call__(self, request, caller=None):
        return SyntheticOwner.__call__(self, request, ('codex-tools-1', 'own'))


class _OwnerRaising(SyntheticOwner):
    """Fault stand-in: fails with an error the driver does not know."""

    def __call__(self, request, caller=None):
        raise RuntimeError('synthetic')


class _FixedOwner(SyntheticOwner):
    """Fault stand-in: serves the given invented rows whatever the question and the scope."""

    def __init__(self, *indexes):
        self._indexes = indexes

    def __call__(self, request, caller=None):
        rows = [{'ref_sha256': sha(b'ref-%d' % index), 'doc_id_sha256': sha(b'doc-%d' % index),
                 'source_event_sha256': self.row_hash(index)} for index in self._indexes]
        return {'schema': RESPONSE_SCHEMA, 'nonce': request['nonce'], 'lane': request['lane'],
                'scope': request['scope'], 'status': 'CANDIDATES' if rows else 'ABSTAIN', 'candidates': rows}


# Lead's counterexample of 15:41Z 11-22 (first case) and its neighbours. Invented rows 0, 1 and 5 are
# written by codex-lead-1, row 2 by codex-tools-1, row 4 by fable-5.
EXPECTED_SCOPE_CASES = {
    'mixed_gold_only_peer_row_served': 'SERVED_OUTSIDE_SCOPE',
    'mixed_gold_own_then_peer_served': 'SERVED_OUTSIDE_SCOPE',
    'mixed_gold_peer_then_own_served': 'SERVED_OUTSIDE_SCOPE',
    'mixed_gold_only_own_row_served': 'HIT_FIRST',
    'mixed_gold_other_own_row_then_own_gold': 'HIT_WITHIN_THREE',
    'mixed_gold_nothing_served': 'MISS_NO_CANDIDATES',
    'peer_gold_only_and_peer_row_served': 'SERVED_OUTSIDE_SCOPE',
    'own_gold_and_peer_row_known_from_another_question': 'SERVED_OUTSIDE_SCOPE',
    'own_gold_and_row_of_unknown_author': 'MISS_WITH_CANDIDATES',
    'negative_control_and_known_peer_row_served': 'SERVED_OUTSIDE_SCOPE',
    'shared_scope_peer_row_is_a_hit': 'HIT_FIRST',
    # Invented row 3 is written by a reviewer lane, which Mind never admits.
    'shared_scope_reviewer_row_served': 'SERVED_OUTSIDE_SCOPE',
    'shared_scope_peer_gold_then_reviewer_row_served': 'SERVED_OUTSIDE_SCOPE',
    'shared_scope_reviewer_gold_only_is_out_of_reach': 'OUT_OF_REACH_WITH_CANDIDATES',
    'shared_scope_reviewer_gold_only_and_nothing_served': 'OUT_OF_REACH_NO_CANDIDATES',
    'shared_scope_mixed_gold_with_reviewer_row_peer_served': 'HIT_FIRST',
    'own_scope_reviewer_gold_only_is_out_of_reach': 'OUT_OF_REACH_NO_CANDIDATES'}


def scope_regression_cases():
    def gold(*indexes):
        return [{'source_event_sha256': SyntheticOwner.row_hash(index), 'agent': SyntheticOwner.ROWS[index][0]}
                for index in indexes]

    lead, reviewer = 'codex-lead-1', 'claude-rco-1'
    mixed = {'id': 'M1', 'language': 'en', 'question': 'synthetic mixed gold', 'expect': 'ANSWER',
             'category': 'synthetic', 'gold': gold(0, 2), 'ask_as': [lead]}
    own_only, peer_only = dict(mixed, id='M2', gold=gold(0)), dict(mixed, id='M4', gold=gold(2))
    negative = dict(mixed, id='M3', expect='ABSTAIN', gold=[])
    authors = {SyntheticOwner.row_hash(index): agent for index, (agent, _) in enumerate(SyntheticOwner.ROWS)}
    with using_scope(EARLIER_SCOPE):  # these cases are about a lane that asks with scope own
        extra = (dict(mixed, id='M5', gold=gold(3), ask_as=[reviewer]),
                 dict(mixed, id='M6', gold=gold(2, 3), ask_as=[reviewer]))
        return _scope_cases(mixed, own_only, peer_only, negative, authors, lead, reviewer, extra)


def _scope_cases(mixed, own_only, peer_only, negative, authors, lead, reviewer, extra):
    reviewer_gold, peer_and_reviewer_gold = extra
    return {
        'mixed_gold_only_peer_row_served': ask(_FixedOwner(2), mixed, lead)[0],
        'mixed_gold_own_then_peer_served': ask(_FixedOwner(0, 2), mixed, lead)[0],
        'mixed_gold_peer_then_own_served': ask(_FixedOwner(2, 0), mixed, lead)[0],
        'mixed_gold_only_own_row_served': ask(_FixedOwner(0), mixed, lead)[0],
        'mixed_gold_other_own_row_then_own_gold': ask(_FixedOwner(1, 0), mixed, lead)[0],
        'mixed_gold_nothing_served': ask(_FixedOwner(), mixed, lead)[0],
        'peer_gold_only_and_peer_row_served': ask(_FixedOwner(2), peer_only, lead)[0],
        'own_gold_and_peer_row_known_from_another_question': ask(_FixedOwner(4), own_only, lead, authors)[0],
        'own_gold_and_row_of_unknown_author': ask(_FixedOwner(4), own_only, lead)[0],
        'negative_control_and_known_peer_row_served': ask(_FixedOwner(2), negative, lead, authors)[0],
        'shared_scope_peer_row_is_a_hit': ask(_FixedOwner(2), dict(mixed, ask_as=[reviewer]), reviewer)[0],
        'shared_scope_reviewer_row_served': ask(_FixedOwner(3), reviewer_gold, reviewer)[0],
        'shared_scope_peer_gold_then_reviewer_row_served':
            ask(_FixedOwner(2, 3), peer_and_reviewer_gold, reviewer)[0],
        'shared_scope_reviewer_gold_only_is_out_of_reach': ask(_FixedOwner(2), reviewer_gold, reviewer)[0],
        'shared_scope_reviewer_gold_only_and_nothing_served': ask(_FixedOwner(), reviewer_gold, reviewer)[0],
        'shared_scope_mixed_gold_with_reviewer_row_peer_served':
            ask(_FixedOwner(2), peer_and_reviewer_gold, reviewer)[0],
        'own_scope_reviewer_gold_only_is_out_of_reach': ask(_FixedOwner(), reviewer_gold, lead)[0]}


def response_fault_cases():
    """Each altered response must be read as a refusal; the unaltered one must be accepted."""
    owner = SyntheticOwner()
    request = build_request('synthetic amber valve', 'claude-rco-2', 'shared')
    good = owner(request)
    assert validate_response(good, request)[0] == 'CANDIDATES' and len(good['candidates']) == 2
    first = good['candidates'][0]
    extra = dict(first, ref_sha256=sha(b'x'))

    def changed(**fields):
        return dict(good, **fields)

    cases = {
        'not_an_object': ['x'],
        'missing_key': {key: value for key, value in good.items() if key != 'scope'},
        'extra_key': changed(text='x'),
        'other_schema': changed(schema='wd.mind-pipe-candidates.v9'),
        'candidates_v2_label': changed(schema='wd.mind-pipe-candidates.v2'),
        'candidates_v3_label': changed(schema='wd.mind-pipe-candidates.v3'),
        'row_with_false_location': changed(candidates=[dict(first, source_length=False)]),
        'rows_with_three_locations': changed(candidates=[
            dict(row, source_offset=1, source_length=2, chunk_index=0) for row in good['candidates']]),
        'other_nonce': changed(nonce=sha(b'other')),
        'other_lane': changed(lane='claude-rco-1'),
        'other_scope': changed(scope='own'),
        'status_without_rows': changed(candidates=[]),
        'rows_without_status': changed(status='ABSTAIN'),
        'unknown_status': changed(status='OK'),
        'four_rows': changed(candidates=good['candidates'] + [extra, dict(extra, ref_sha256=sha(b'y'))]),
        'short_hash': changed(candidates=[dict(first, source_event_sha256='abc')]),
        'capital_hash': changed(candidates=[dict(first, source_event_sha256=first['source_event_sha256'].upper())]),
        'row_missing_key': changed(candidates=[{key: value for key, value in first.items()
                                                if key != 'doc_id_sha256'}]),
        'row_with_text': changed(candidates=[dict(first, text='x')]),
        'duplicate_row': changed(candidates=[first, dict(first)]),
        'rows_not_a_list': changed(candidates='x')}
    outcome = {}
    for name, response in cases.items():
        try:
            validate_response(response, request)
            outcome[name] = 'ACCEPTED'
        except DriverError as error:
            outcome[name] = 'refused:' + str(error)
    return outcome


def product_table_problems():
    """The twelve invented asks under the active product table against EXPECTED_PRODUCT."""
    card, rows = run(SyntheticOwner(), synthetic_sets(), LANES)
    problems = []
    per_ask = {row['id']: row['class'] for row in rows}
    if per_ask != EXPECTED_PRODUCT['per_ask']:
        problems.append(('product_per_ask', {key: (per_ask.get(key), want) for key, want in
                                             EXPECTED_PRODUCT['per_ask'].items() if per_ask.get(key) != want}))
    for name, part in (('all', card['all']), ('mine', card['questions_by_me']),
                       ('others', card['questions_by_others'])):
        for key, want in EXPECTED_PRODUCT[name].items():
            if part[key] != want:
                problems.append(('product_' + name, key, part[key], want))
    if card['refusal_reasons'] != EXPECTED_PRODUCT['refusal_reasons']:
        problems.append(('product_refusal_reasons', card['refusal_reasons']))
    if set(SCOPE.values()) != {'shared'}:
        problems.append(('product_table_is_not_shared_for_all', dict(SCOPE)))
    return problems, card


def selftest():
    product_problems, product_card = product_table_problems()
    with using_scope(EARLIER_SCOPE):  # the earlier table keeps the own-scope classes under test
        return _selftest_earlier_table(product_problems, product_card)


def _selftest_earlier_table(product_problems, product_card):
    sets = synthetic_sets()
    card, rows = run(SyntheticOwner(), sets, LANES)
    problems = list(product_problems)
    per_ask = {row['id']: row['class'] for row in rows}
    if per_ask != EXPECTED_SELFTEST['per_ask']:
        problems.append(('per_ask', {key: (per_ask.get(key), want) for key, want in
                                     EXPECTED_SELFTEST['per_ask'].items() if per_ask.get(key) != want}))
    for name, part in (('all', card['all']), ('mine', card['questions_by_me']),
                       ('others', card['questions_by_others'])):
        for key, want in EXPECTED_SELFTEST[name].items():
            if part[key] != want:
                problems.append((name, key, part[key], want))
    if card['refusal_reasons'] != EXPECTED_SELFTEST['refusal_reasons']:
        problems.append(('refusal_reasons', card['refusal_reasons']))
    faults = response_fault_cases()
    accepted = sorted(name for name, result in faults.items() if result == 'ACCEPTED')
    if accepted:
        problems.append(('altered_response_accepted', accepted))
    not_bound, _ = run(PipeV3NotBound(), sets, LANES)
    if (not_bound['all']['classes']['REFUSED'] != not_bound['all']['asks']
            or set(not_bound['refusal_reasons']) != {'route_not_bound'}
            or not_bound['all']['hit_first_rate'] != 0.0 or not_bound['all']['negative_pass_rate'] != 0.0):
        problems.append(('unbound_route_not_refused', not_bound['all']['classes']))
    item = {question['id']: question for document, _ in sets for question in document['questions']}
    route_faults = {
        'owner_ignoring_scope': ask(_OwnerIgnoringScope(), item['A5'], 'codex-lead-1')[:2],
        'owner_changing_request': ask(_OwnerChangingRequest(), item['A1'], 'codex-lead-1')[:2],
        'owner_raising': ask(_OwnerRaising(), item['A1'], 'codex-lead-1')[:2],
        'caller_identity_of_another_lane': ask(_RouteGivingAnotherCaller(), item['A1'], 'codex-lead-1')[:2]}
    wanted = {'owner_ignoring_scope': ('SERVED_OUTSIDE_SCOPE', ''),
              'owner_changing_request': ('REFUSED', 'request_changed_by_route'),
              'owner_raising': ('REFUSED', 'route_raised_RuntimeError'),
              'caller_identity_of_another_lane': ('REFUSED', 'synthetic_owner_refused')}
    if route_faults != wanted:
        problems.append(('route_faults', route_faults))
    scope_cases = scope_regression_cases()
    if scope_cases != EXPECTED_SCOPE_CASES:
        problems.append(('scope_cases', {name: (scope_cases.get(name), want) for name, want in
                                         EXPECTED_SCOPE_CASES.items() if scope_cases.get(name) != want}))
    stable = {key: card[key] for key in ('route', 'label', 'request_shape', 'all', 'questions_by_me',
                                         'questions_by_others', 'by_lane', 'by_language', 'by_category',
                                         'refusal_reasons')}
    result = {'selftest': 'MATCH' if not problems else 'MISMATCH', 'problems': problems,
              'asks': card['all']['asks'], 'classes': card['all']['classes'],
              'product_table': {'scope': dict(PRODUCT_SCOPE), 'classes': product_card['all']['classes'],
                                'answerable_asks_in_reach': product_card['all']['answerable_asks_in_reach'],
                                'hit_first_rate': product_card['all']['hit_first_rate'],
                                'hit_within_three_rate': product_card['all']['hit_within_three_rate']},
              'altered_responses_refused': '%d of %d' % (len(faults) - len(accepted), len(faults)),
              'altered_response_reasons': faults,
              'scope_cases': scope_cases,
              'scope_cases_sha256': sha(json.dumps(scope_cases, sort_keys=True, separators=(',', ':')).encode()),
              'route_fault_cases': {name: list(value) for name, value in route_faults.items()},
              'unbound_route': '%d of %d asks refused' % (not_bound['all']['classes']['REFUSED'],
                                                          not_bound['all']['asks']),
              'synthetic_card_sha256': sha(json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()),
              'driver_sha256': card['driver_sha256'], 'request_shape': card['request_shape']}
    print(json.dumps(result, indent=1))
    print('SELFTEST MATCH' if not problems else 'SELFTEST MISMATCH')
    return 0 if not problems else 1


# ---------------------------------------------------------------- command line

def main(argv):
    if argv == ['selftest']:
        return selftest()
    if argv[:1] == ['check'] and len(argv) > 1:
        for path in argv[1:]:
            document, digest = load_set(path)
            questions = document['questions']
            print(json.dumps({'set_id': document['set_id'], 'author': document['author'], 'file_sha256': digest,
                              'questions': len(questions),
                              'expect': dict(Counter(item['expect'] for item in questions)),
                              'language': dict(Counter(item['language'] for item in questions)),
                              'ask_as_any': sum(item['ask_as'] == 'any' for item in questions),
                              'gold_rows': sum(len(item['gold']) for item in questions),
                              'gold_authors': dict(Counter(row['agent'] for item in questions
                                                           for row in item['gold']))}))
        return 0
    if argv[:2] == ['convert-v1', '--private-root'] and len(argv) == 5:
        document, digest = convert_v1(argv[3], argv[4], argv[2])
        print(json.dumps({'written': 'private set file', 'file_sha256': digest,
                          'questions': len(document['questions'])}))
        return 0
    if argv[:1] == ['run']:
        options, paths, rest = {}, [], argv[1:]
        while rest:
            if rest[0] in ('--route', '--lanes', '--out', '--private-root') and len(rest) > 1:
                options[rest[0]], rest = rest[1], rest[2:]
            else:
                paths.append(rest[0])
                rest = rest[1:]
        lanes = tuple(options.get('--lanes', ','.join(LANES)).split(','))
        if (options.get('--route') not in ROUTES or not paths or '--out' not in options
                or '--private-root' not in options
                or any(lane not in LANES for lane in lanes) or len(set(lanes)) != len(lanes)):
            raise DriverError('usage')
        sets = [load_set(path) for path in paths]
        out = private_target(options['--out'], options['--private-root'])
        out.mkdir(parents=True, exist_ok=False)
        card, rows = run(ROUTES[options['--route']](), sets, lanes)
        (out / 'card.json').write_bytes(json.dumps(card, indent=1).encode('utf-8'))
        (out / 'rows.private.json').write_bytes(json.dumps(rows, indent=1).encode('utf-8'))
        print(json.dumps({key: card[key] for key in ('route', 'label', 'all', 'questions_by_others',
                                                     'questions_by_me', 'refusal_reasons', 'milliseconds')},
                         indent=1))
        return 0
    raise DriverError('usage')


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except DriverError as error:
        print('REFUSED: %s' % error)
        sys.exit(2)
