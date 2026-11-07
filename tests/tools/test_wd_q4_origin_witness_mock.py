"""Synthetic data-only registry contract. No runtime transport or real packages."""

import builtins
import types

import pytest

from tools.wd_q4_origin_witness_mock import INCOMPLETE, REGISTERED, Incomplete, MockOriginWitness


def module(name):
    return types.ModuleType(name)


def registered():
    value = module("root")
    witness = MockOriginWitness()
    witness.register("root", value)
    world = {"root": value}
    witness.seal(world)
    return witness, value, world


def test_registration_has_no_authority_before_seal():
    witness = MockOriginWitness()
    value = module("root")
    witness.register("root", value)
    assert witness.classify("root", value, {"root": value}) == INCOMPLETE


def test_registered_exact_data_object_is_recognized():
    witness, value, world = registered()
    assert witness.classify("root", value, world) == REGISTERED
    assert witness.classify("root", value, dict(world)) == REGISTERED


@pytest.mark.parametrize("field", ["__file__", "__name__", "new_field"])
def test_metadata_change_invalidates_sealed_witness(field):
    witness, value, world = registered()
    value.__dict__[field] = "changed"
    assert witness.classify("root", value, world) == INCOMPLETE


def test_same_name_replacement_is_incomplete_and_cannot_be_reselected():
    witness, original, world = registered()
    replacement = module("root")
    world["root"] = replacement
    assert witness.classify("root", replacement, world) == INCOMPLETE
    world["root"] = original
    assert witness.classify("root", original, world) == INCOMPLETE


@pytest.mark.parametrize("name", ["cython_runtime", "torch.ops", "transformers.synthetic"])
def test_names_and_origin_labels_never_enroll_an_unknown_object(name):
    witness, value, world = registered()
    unknown = module(name)
    unknown.__file__ = "claimed-trusted.py"
    assert witness.classify(name, unknown, world) == INCOMPLETE
    assert witness.classify("root", unknown, world) == INCOMPLETE
    assert witness.classify("root", value, world) == REGISTERED


@pytest.mark.parametrize("origin", ["built-in", "frozen"])
def test_forged_spec_is_not_a_registration_proof(origin):
    value = module("root")
    value.__spec__ = types.SimpleNamespace(origin=origin)
    witness = MockOriginWitness()
    with pytest.raises(Incomplete):
        witness.register("root", value)
    assert witness.classify("root", value, {"root": value}) == INCOMPLETE


@pytest.mark.parametrize("field", ["__getattr__", "__dir__"])
def test_callable_metadata_remains_unsupported_without_executing_it(field):
    calls = []
    value = module("root")

    def hook(*args):
        calls.append(args)
        return None

    value.__dict__[field] = hook
    witness = MockOriginWitness()
    with pytest.raises(Incomplete):
        witness.register("root", value)
    assert calls == []


def test_module_subclass_is_refused_without_attribute_hooks():
    calls = []

    class Hooked(types.ModuleType):
        def __getattribute__(self, name):
            calls.append(name)
            return types.ModuleType.__getattribute__(self, name)

    value = Hooked("root")
    witness = MockOriginWitness()
    with pytest.raises(Incomplete):
        witness.register("root", value)
    assert calls == []


@pytest.mark.parametrize("value", [[], {}, object()])
def test_unknown_or_mutable_metadata_is_not_silently_trusted(value):
    item = module("root")
    item.payload = value
    with pytest.raises(Incomplete):
        MockOriginWitness().register("root", item)


def test_metadata_equality_hooks_are_not_called():
    calls = []

    class EqualityHook:
        def __eq__(self, other):
            calls.append(other)
            raise AssertionError("unexpected equality")

    item = module("root")
    item.payload = EqualityHook()
    with pytest.raises(Incomplete):
        MockOriginWitness().register("root", item)
    assert calls == []


def test_unknown_metadata_type_never_invokes_metaclass_equality():
    calls = []

    class EqualityMeta(type):
        def __eq__(cls, other):
            calls.append(other)
            raise AssertionError("unexpected metaclass equality")

    class Unknown(metaclass=EqualityMeta):
        pass

    item = module("root")
    item.payload = Unknown()
    with pytest.raises(Incomplete):
        MockOriginWitness().register("root", item)
    assert calls == []


def test_nonstring_namespace_key_is_refused_without_hash_or_equality_hooks():
    calls = []

    class Key:
        def __hash__(self):
            calls.append("hash")
            return 7

        def __eq__(self, other):
            calls.append("equal")
            return False

    item = module("root")
    item.__dict__[Key()] = None
    calls.clear()
    with pytest.raises(Incomplete):
        MockOriginWitness().register("root", item)
    assert calls == []


def test_nonstring_world_key_is_refused_without_hash_or_equality_hooks():
    calls = []

    class Key:
        def __hash__(self):
            calls.append("hash")
            return 7

        def __eq__(self, other):
            calls.append("equal")
            return False

    item = module("root")
    witness = MockOriginWitness()
    witness.register("root", item)
    world = {"root": item, Key(): item}
    calls.clear()
    with pytest.raises(Incomplete):
        witness.seal(world)
    assert calls == []


def test_registered_parent_child_graph_is_consistent():
    parent, child = module("root"), module("root.child")
    parent.child = child
    witness = MockOriginWitness()
    witness.register("root", parent)
    witness.register("root.child", child, parent=("root", "child"))
    world = {"root": parent, "root.child": child}
    witness.seal(world)
    assert witness.classify("root.child", child, world) == REGISTERED
    assert witness.classify("root", parent, world) == REGISTERED


def test_broken_parent_edge_is_not_accepted():
    parent, child = module("root"), module("root.child")
    parent.child = child
    witness = MockOriginWitness()
    witness.register("root", parent)
    witness.register("root.child", child, parent=("root", "child"))
    world = {"root": parent, "root.child": child}
    witness.seal(world)
    parent.child = module("root.child")
    assert witness.classify("root.child", child, world) == INCOMPLETE


def test_unregistered_module_reference_does_not_complete_a_graph():
    parent = module("root")
    parent.child = module("root.child")
    witness = MockOriginWitness()
    witness.register("root", parent)
    with pytest.raises(Incomplete):
        witness.seal({"root": parent})


def test_parent_cycle_terminates_incomplete():
    one, two = module("one"), module("two")
    one.next, two.previous = two, one
    witness = MockOriginWitness()
    witness.register("one", one, parent=("two", "previous"))
    witness.register("two", two, parent=("one", "next"))
    with pytest.raises(Incomplete):
        witness.seal({"one": one, "two": two})


def test_parent_depth_budget_is_enforced():
    root, child, leaf = module("root"), module("child"), module("leaf")
    root.child, child.leaf = child, leaf
    witness = MockOriginWitness(max_depth=1)
    witness.register("root", root)
    witness.register("child", child, parent=("root", "child"))
    witness.register("leaf", leaf, parent=("child", "leaf"))
    with pytest.raises(Incomplete):
        witness.seal({"root": root, "child": child, "leaf": leaf})


def test_entry_budget_is_enforced_without_retry_loop():
    witness = MockOriginWitness(max_entries=1)
    witness.register("one", module("one"))
    with pytest.raises(Incomplete):
        witness.register("two", module("two"))
    assert witness.classify("one", module("one"), {}) == INCOMPLETE


@pytest.mark.parametrize("budget", [0, -1, True, 129, 1.5])
def test_invalid_entry_budgets_are_rejected(budget):
    with pytest.raises(ValueError):
        MockOriginWitness(max_entries=budget)


@pytest.mark.parametrize("budget", [0, -1, True, 65, 1.5])
def test_invalid_depth_budgets_are_rejected(budget):
    with pytest.raises(ValueError):
        MockOriginWitness(max_depth=budget)


def test_duplicate_registration_fails_closed():
    value = module("root")
    witness = MockOriginWitness()
    witness.register("root", value)
    with pytest.raises(Incomplete):
        witness.register("root", value)
    assert witness.classify("root", value, {"root": value}) == INCOMPLETE


def test_postseal_registration_cannot_extend_a_witness():
    witness, value, world = registered()
    with pytest.raises(Incomplete):
        witness.register("later", module("later"))
    assert witness.classify("root", value, world) == INCOMPLETE


def test_empty_registry_does_not_become_an_acceptance():
    witness = MockOriginWitness()
    with pytest.raises(Incomplete):
        witness.seal({})
    assert witness.classify("root", module("root"), {}) == INCOMPLETE


def test_extra_world_binding_is_not_silently_enrolled():
    witness, value, world = registered()
    world["extra"] = module("extra")
    assert witness.classify("root", value, world) == INCOMPLETE


def test_wrong_name_is_not_registered():
    with pytest.raises(Incomplete):
        MockOriginWitness().register("claimed", module("different"))


def test_lifecycle_uses_no_import_or_file_io(monkeypatch):
    value = module("root")
    witness = MockOriginWitness()
    world = {"root": value}

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O or import")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(builtins, "__import__", forbidden)
        witness.register("root", value)
        witness.seal(world)
        result = witness.classify("root", value, world)
    assert result == REGISTERED

