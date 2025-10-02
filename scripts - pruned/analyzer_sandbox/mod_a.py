from analyzer_sandbox.mod_b import greet

def unused_func():
    return "dead"

def main():
    print(greet("Baris"))

if __name__ == "__main__":
    main()
