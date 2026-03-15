"""Cottage profile demo — sample queries for testing the routing pipeline.

Run:
    python examples/cottage_demo/demo_queries.py
"""

DEMO_QUERIES = [
    # Bee QA (model-based routing)
    {"query": "How much honey can a strong colony produce?", "expected": "model_based"},
    {"query": "Kuinka paljon hunajaa vahva pesä tuottaa?", "expected": "model_based"},
    {"query": "When should I treat for varroa?", "expected": "model_based"},

    # Seasonal (seasonal routing)
    {"query": "What should I do in March for my bees?", "expected": "seasonal"},
    {"query": "Mitä tehdä mehiläisille huhtikuussa?", "expected": "seasonal"},

    # Rule-based (constraint engine)
    {"query": "Is 38°C too hot for the hive?", "expected": "rule_constraints"},

    # Statistical
    {"query": "What's the average temperature today?", "expected": "statistical"},

    # Math
    {"query": "Calculate 3 kertaa 7 plus 2", "expected": "math"},

    # Retrieval (FAISS)
    {"query": "What is varroa destructor?", "expected": "retrieval"},

    # General LLM fallback
    {"query": "Tell me a joke about bees", "expected": "llm_reasoning"},
]


def main():
    print("WaggleDance Cottage Demo — Query Samples")
    print("=" * 50)
    for i, q in enumerate(DEMO_QUERIES, 1):
        print(f"\n{i}. [{q['expected']}]")
        print(f"   {q['query']}")


if __name__ == "__main__":
    main()
