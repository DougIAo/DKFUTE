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
}

cancelSendLinkBtn.addEventListener('click', () => {
    linkModal.classList.add('hidden');
    streamLinkInput.value = ''; // Limpa o input
});

confirmSendLinkBtn.addEventListener('click', async () => {
    const streamLink = streamLinkInput.value;
    if (!streamLink) {
        alert('Por favor, insira o link da transmissão.');
        return;
    }

    if (!currentRegId || !currentWhatsapp) {
        alert('Erro: ID do registro ou número do WhatsApp não definido.');
        linkModal.classList.add('hidden');
        return;
    }

    try {
        const response = await fetch(`/admin/send_link/${currentRegId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                // Se você tiver CSRF tokens, adicione aqui
            }
        });
        const data = await response.json();

        if (data.success) {
            // Atualizar UI na tabela
            const linkStatusCell = document.querySelector(`.link-status-${currentRegId}`);
            if (linkStatusCell) {
                linkStatusCell.innerHTML = '<span class="text-green-500"><i class="fas fa-check-circle"></i> Sim</span>';
            }
            const button = document.querySelector(`.send-link-btn-${currentRegId}`);
            if (button) {
                button.disabled = true;
                button.innerHTML = '<i class="fab fa-whatsapp"></i> Link Enviado';
            }

            // Abrir WhatsApp Web com mensagem pré-preenchida
            const cleanWhatsapp = currentWhatsapp.replace(/\D/g, ''); // Remove não dígitos
            const message = encodeURIComponent(`Olá! Segue o seu link para a transmissão do jogo: ${streamLink}\n\nObrigado por comprar conosco!`);
            const whatsappUrl = `https://wa.me/55${cleanWhatsapp}?text=${message}`; // Adiciona 55 para Brasil
            
            window.open(whatsappUrl, '_blank');
            
            // Opcional: fechar modal e limpar
            linkModal.classList.add('hidden');
            streamLinkInput.value = '';
            // Recarregar a página para refletir mudanças ou mostrar flash message do Flask
            // window.location.reload(); // descomente se quiser recarregar a página

        } else {
            alert('Erro ao marcar link como enviado: ' + (data.error || 'Erro desconhecido'));
        }
    } catch (error) {
        console.error('Erro na requisição:', error);
        alert('Ocorreu um erro de comunicação com o servidor.');
    }
});

// Fechar modal se clicar fora (opcional)
linkModal.addEventListener('click', (event) => {
    if (event.target === linkModal) { // Clicou no fundo escuro
        linkModal.classList.add('hidden');
        streamLinkInput.value = '';
    }
});