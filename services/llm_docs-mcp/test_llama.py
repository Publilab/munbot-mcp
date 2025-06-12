from llama_cpp import Llama

llm = Llama.from_pretrained(
    repo_id="bartowski/Llama-3.2-3B-Instruct-GGUF",
    filename="Llama-3.2-3B-Instruct-Q6_K.gguf",
    local_dir="./models",
    verbose=True
)

response = llm.create_chat_completion(
    messages=[{"role": "user", "content": "¿Hola?"}],
    max_tokens=32
)
print(response["choices"][0]["message"]["content"]) 