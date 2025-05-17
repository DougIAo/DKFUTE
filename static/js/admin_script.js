const linkModal = document.getElementById('linkModal');
const streamLinkInput = document.getElementById('streamLinkInput');
const confirmSendLinkBtn = document.getElementById('confirmSendLinkBtn');
const cancelSendLinkBtn = document.getElementById('cancelSendLinkBtn');
let currentRegId = null;
let currentWhatsapp = null;

function sendLink(registrationId, whatsappNumber) {
    currentRegId = registrationId;
    currentWhatsapp = whatsappNumber;
    linkModal.classList.remove('hidden');
    streamLinkInput.focus(); // Foca no input ao abrir o modal
}

if (cancelSendLinkBtn) {
    cancelSendLinkBtn.addEventListener('click', () => {
        linkModal.classList.add('hidden');
        streamLinkInput.value = ''; // Limpa o input
    });
}

if (confirmSendLinkBtn) {
    confirmSendLinkBtn.addEventListener('click', async () => {
        const streamLink = streamLinkInput.value.trim();
        if (!streamLink) {
            alert('Por favor, insira o link da transmissão.');
            streamLinkInput.focus();
            return;
        }

        if (!currentRegId || !currentWhatsapp) {
            alert('Erro: ID do registro ou número do WhatsApp não definido.');
            linkModal.classList.add('hidden');
            return;
        }

        // Desabilitar botões do modal durante o processamento
        confirmSendLinkBtn.disabled = true;
        confirmSendLinkBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Enviando...';
        cancelSendLinkBtn.disabled = true;

        try {
            const response = await fetch(`/admin/send_link/${currentRegId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    // Se você tiver CSRF tokens no Flask-WTF, adicione o header X-CSRFToken aqui
                }
                // Não precisamos enviar body para esta rota específica, apenas atualizamos o status
            });
            const data = await response.json();

            if (response.ok && data.success) {
                // Atualizar UI na tabela
                const linkStatusCell = document.querySelector(`.link-status-${currentRegId}`);
                if (linkStatusCell) {
                    linkStatusCell.innerHTML = '<span class="text-green-500 font-semibold"><i class="fas fa-check-circle mr-1"></i> Sim</span>';
                }
                const button = document.querySelector(`.send-link-btn-${currentRegId}`);
                if (button) {
                    button.disabled = true;
                    button.innerHTML = '<i class="fab fa-whatsapp"></i> Link Enviado';
                    button.title = "Link já foi enviado para este registro.";
                }

                // Abrir WhatsApp Web com mensagem pré-preenchida
                const cleanWhatsapp = currentWhatsapp.replace(/\D/g, ''); // Remove não dígitos
                const message = encodeURIComponent(`Olá! Segue o seu link para a transmissão do jogo: ${streamLink}\n\nObrigado por comprar conosco!`);
                const whatsappUrl = `https://wa.me/55${cleanWhatsapp}?text=${message}`; // Adiciona 55 para Brasil
                
                window.open(whatsappUrl, '_blank');
                
                // Fechar modal e limpar
                linkModal.classList.add('hidden');
                streamLinkInput.value = '';
                // Opcional: Mostrar flash message via JS se o backend retornar uma mensagem
                // ou recarregar para ver flash messages do Flask (se houver)
                // window.location.reload(); 
                
            } else {
                alert('Erro ao marcar link como enviado: ' + (data.error || response.statusText || 'Erro desconhecido'));
            }
        } catch (error) {
            console.error('Erro na requisição:', error);
            alert('Ocorreu um erro de comunicação com o servidor.');
        } finally {
            // Reabilitar botões do modal
            confirmSendLinkBtn.disabled = false;
            confirmSendLinkBtn.innerHTML = '<i class="fab fa-whatsapp mr-2"></i>Abrir WhatsApp';
            cancelSendLinkBtn.disabled = false;
        }
    });
}

// Fechar modal se clicar fora (opcional)
if (linkModal) {
    linkModal.addEventListener('click', (event) => {
        if (event.target === linkModal) { // Clicou no fundo escuro
            linkModal.classList.add('hidden');
            streamLinkInput.value = '';
        }
    });
    // Fechar modal com a tecla ESC
    document.addEventListener('keydown', (event) => {
        if (event.key === "Escape" && !linkModal.classList.contains('hidden')) {
            linkModal.classList.add('hidden');
            streamLinkInput.value = '';
        }
    });
}
