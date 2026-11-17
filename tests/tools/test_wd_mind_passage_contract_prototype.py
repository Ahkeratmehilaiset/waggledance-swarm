"""Prove compatibility of the synthetic contract and expose the bypass."""
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_full_runtime as runtime

_here=Path(__file__).with_name('mind_passage_contract_prototype.py')
spec=importlib.util.spec_from_file_location('synthetic_passage_contract',_here)
contract=importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)


def test_synthetic_passage_and_record_binding_match_current_runtime():
    record={'record_id':'synthetic-1','stored_text':'ordinary synthetic content'}
    assert contract.passage(record,reader.decode_text)=='passage: ordinary synthetic content'
    assert contract.record_keys(record,reader.decode_text)==runtime._cache_record_keys(record)
    vector=b'v'*1536
    assert contract.pair_vectors((record,),(vector,),reader.decode_text)==((*runtime._cache_record_keys(record),vector),)


def test_duplicate_records_and_wrong_vector_lengths_are_refused():
    record={'record_id':'synthetic-1','stored_text':'ordinary synthetic content'}
    with pytest.raises(ValueError,match='pairs'):
        contract.pair_vectors((record,record),(b'x'*1536,b'y'*1536),reader.decode_text)
    with pytest.raises(ValueError,match='pairs'):
        contract.pair_vectors((record,),(b'bad',),reader.decode_text)


def _actual_runtime_passage_expression():
    tree=ast.parse(Path(runtime.__file__).read_text(encoding='utf-8'))
    matches=[]
    for node in ast.walk(tree):
        if (isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='texts' for t in node.targets)
                and 'passage:' in ast.unparse(node.value) and 'decode_text' in ast.unparse(node.value)):
            matches.append(node.value)
    assert len(matches)>=2, 'production builders changed; reassess the bypass test'
    return matches[0]


def _evaluate(expression, record):
    code=compile(ast.fix_missing_locations(ast.Expression(body=expression)),'<synthetic-runtime-expression>','eval')
    return eval(code,{'tuple':tuple,'reader':reader,'by_id':{'synthetic-1':record},'chosen':('synthetic-1',)})[0]


def test_current_uncontracted_runtime_can_change_passage_without_changing_contract_key():
    """Concrete mutation of the actual builder expression; no production edit."""
    record={'record_id':'synthetic-1','stored_text':'ordinary synthetic content'}
    expression=_actual_runtime_passage_expression()
    original=_evaluate(copy.deepcopy(expression),record)
    class RewritePassage(ast.NodeTransformer):
        def visit_Constant(self,node):
            return ast.copy_location(ast.Constant('passage: changed '),node) if node.value=='passage: ' else node
    changed=_evaluate(RewritePassage().visit(copy.deepcopy(expression)),record)
    assert original==contract.passage(record,reader.decode_text)
    assert changed!=original
    # Simulate a cache keyed only by the unchanged contract + current record.
    # Current runtime itself constructs passage text separately from keying.
    key=contract.record_keys(record,reader.decode_text)
    cached={key:hashlib.sha256(original.encode()).digest()}
    assert cached[contract.record_keys(record,reader.decode_text)]!=hashlib.sha256(changed.encode()).digest()
