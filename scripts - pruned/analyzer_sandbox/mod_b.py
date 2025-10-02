def greet(name: str) -> str:
    return f"Hello, {name}"

def dangerous(code):
    return eval(code)  # Bandit should flag: B307 (eval)
