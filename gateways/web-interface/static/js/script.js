document.addEventListener('DOMContentLoaded', () => {
    console.log("script.js cargado correctamente");

    // Conectar al servidor WebSocket
    const socket = io(); // Usa la URL relativa para que el navegador resuelva correctamente
    console.log("Conectado al servidor WebSocket");

    const chatContainer = document.getElementById('chat-container');
    const chatToggle = document.getElementById('chat-toggle');
    const chatBody = document.getElementById('chat-body');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');
    const chatHeader = document.getElementById('chat-header');

    // Verificar si los elementos fueron encontrados
    if (!chatContainer || !chatToggle || !chatBody || !messageInput || !sendButton || !chatHeader) {
        console.error("Uno o más elementos del DOM no fueron encontrados");
        return;
    }

    // Mostrar/Ocultar la ventana de chat al hacer clic en el botón
    chatToggle.addEventListener('click', () => {
        chatContainer.classList.toggle('hidden');
    });

    // También se puede ocultar/mostrar la ventana al hacer clic en el header (opcional)
    chatHeader.addEventListener('click', () => {
        chatContainer.classList.toggle('hidden');
    });

    // Enviar mensaje al hacer clic en el botón
    sendButton.addEventListener('click', sendMessage);

    // Enviar mensaje al presionar Enter
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });

    // Detectar si el usuario sigue escribiendo para extender el debounce en el backend
    messageInput.addEventListener('input', () => {
        const hasText = messageInput.value.trim().length > 0;
        socket.emit('typing', { hasText });
    });

    // Función para enviar mensajes
    function sendMessage() {
        const message = messageInput.value.trim();
        if (message === '') {
            console.log("Mensaje vacío, no se enviará");
            return;
        }
        console.log("Mensaje enviado:", message);
        appendMessage(message, 'user');
        socket.emit('message', message);
        messageInput.value = '';
        socket.emit('typing', { hasText: false });
    }

    // Función para añadir mensajes al chat
    function appendMessage(message, sender) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', sender);
        messageElement.innerHTML = message;
        chatBody.appendChild(messageElement);
        chatBody.scrollTop = chatBody.scrollHeight;
    }

    // Función para añadir mensajes del sistema
    function appendSystemMessage(message) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', 'system');
        messageElement.innerHTML = message;
        chatBody.appendChild(messageElement);
        chatBody.scrollTop = chatBody.scrollHeight;
    }

    // Declarar una cola para mensajes del bot
    let botMessageQueue = [];
    let processingBotQueue = false;

    // Al recibir un mensaje del bot, agregarlo a la cola
    socket.on('bot_message', (msg) => {
        botMessageQueue.push({ text: msg, replies: null });
        processBotQueue();
    });

    // Al recibir payload estructurado, agregarlo a la cola
    socket.on('bot_payload', (payload) => {
        botMessageQueue.push({ text: payload.respuesta, replies: payload.suggested_replies });
        processBotQueue();
    });

    // Función para agregar el indicador de "escribiendo" usando animación CSS
    function showTypingIndicator() {
        const typingIndicator = document.createElement('div');
        typingIndicator.id = 'typing-indicator';
        typingIndicator.classList.add('typing-indicator');
        // Inserta la estructura de tres puntos
        typingIndicator.innerHTML = '<span></span><span></span><span></span>';
        chatBody.appendChild(typingIndicator);
        chatBody.scrollTop = chatBody.scrollHeight;
    }

    // Función para remover el indicador de "escribiendo"
    function removeTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            chatBody.removeChild(indicator);
        }
    }

    // Función que procesa la cola de mensajes con un retardo y muestra el indicador
    function processBotQueue() {
        if (processingBotQueue || botMessageQueue.length === 0) {
            return;
        }
        processingBotQueue = true;
        
        // Mostrar el indicador de escritura
        showTypingIndicator();
        
        // Esperar antes de mostrar el siguiente mensaje
        setTimeout(() => {
            // Remover el indicador de escritura
            removeTypingIndicator();
            const nextItem = botMessageQueue.shift();
            if (nextItem.text) {
                appendMessage(nextItem.text, 'bot');
            }
            if (nextItem.replies) {
                renderSuggestedReplies(nextItem.replies);
            }
            processingBotQueue = false;
            processBotQueue();
        }, 2000);
    }

    function handleConnectionError() {
        alert('Hubo un error, reiniciando el chat...');
        window.location.reload();
    }

    // Renderizar botones sugeridos en el chat (solo Web)
    function renderSuggestedReplies(options) {
        // Eliminar bloques anteriores de sugerencias
        const oldBlocks = chatBody.querySelectorAll('.suggested-block');
        oldBlocks.forEach(b => b.remove());

        if (!Array.isArray(options) || options.length === 0) return;
        const block = document.createElement('div');
        block.classList.add('suggested-block');

        options.forEach((label) => {
            const btn = document.createElement('button');
            btn.classList.add('suggested-button');
            btn.textContent = label;
            btn.addEventListener('click', () => {
                // Enviar el texto literal del botón
                appendMessage(label, 'user');
                socket.emit('message', label);
                // Limpiar opciones luego del click
                block.remove();
            });
            block.appendChild(btn);
        });
        chatBody.appendChild(block);
        chatBody.scrollTop = chatBody.scrollHeight;
    }
});
