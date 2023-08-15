import asyncio
from random import randint
from time import time
from tcputils import *


class Servidor:
    def __init__(self, rede, porta):
        self.rede = rede
        self.porta = porta
        self.conexoes = {}
        self.callback = None
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que uma nova conexão for aceita
        """
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        src_port, dst_port, n_seq, n_ack, \
            flags, window_size, checksum, urg_ptr = read_header(segment)

        if dst_port != self.porta:
            # Ignora segs que não são destinados à porta do nosso servidor
            return
        if not self.rede.ignore_checksum and calc_checksum(segment, src_addr, dst_addr) != 0:
            print('descartando seg com checksum incorreto')
            return

        payload = segment[4*(flags>>12):]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        if (flags & FLAGS_SYN) == FLAGS_SYN:
            # A flag SYN estar setada significa que é um cliente tentando estabelecer uma conexão nova
            # TODO: talvez você precise passar mais coisas para o construtor de conexão
        
            # TODO: você precisa fazer o handshake aceitando a conexão. Escolha se você acha melhor
            # fazer aqui mesmo ou dentro da classe Conexao.

            # PASSO 1
            conexao = self.conexoes[id_conexao] = Conexao(self, id_conexao)
            
            #flags
            flags = flags & 0
            flags = flags | (FLAGS_SYN | FLAGS_ACK)

            # Numero aleatorio para n_ack
            conexao.n_seq = randint(0, 0xffff)
            conexao.n_ack = n_seq + 1

            # Inversão do endereço de origem e destino
            src_port, dst_port = dst_port, src_port
            src_addr, dst_addr = dst_addr, src_addr

            # Header com flags SYN e ACK
            seg = make_header(src_port, dst_port, conexao.n_seq, conexao.n_ack, flags)
            seg_fixed_checksum = fix_checksum(seg, src_addr, dst_addr)
            self.rede.enviar(seg_fixed_checksum, dst_addr)

            # Incrementando n_seq 
            conexao.n_seq += 1
            conexao.n_seq_base = conexao.n_seq
            
            if self.callback:
                self.callback(conexao)

        elif id_conexao in self.conexoes:
            # Passa para a conexão adequada se ela já estiver estabelecida
            self.conexoes[id_conexao]._rdt_rcv(n_seq, n_ack, flags, payload)
        else:
            print('%s:%d -> %s:%d (pacote associado a conexão desconhecida)' %
                  (src_addr, src_port, dst_addr, dst_port))


class Conexao:
    def __init__(self, servidor, id_conexao):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.n_ack = None
        self.n_seq = None
        self.callback = None
        self.n_seq_base = None
        self.pck_sem_ack = []
        self.estRTT = None
        self.timer = None
        self.timeout_int = 1
        self.fechada = False
        self.cwnd = MSS # tamanho da janela de congestionamento
        self.last_ack_received = 0
        self.acked_segments = 0

    def _rdt_rcv(self, n_seq, n_ack, flags, payload):
        # TODO: trate aqui o recebimento de segs provenientes da camada de rede.
        # Chame self.callback(self, dados) para passar dados para a camada de aplicação após
        # garantir que eles não sejam duplicados e que tenham sido recebidos em ordem.
        # print('recebido payload: %r' % payload)

        if self.fechada:
            return


        # Passo 2
        # QUando flag = ACK, precisa parar o timer e tirar da lista de packets a serem confirmados
        if (flags & FLAGS_ACK) == FLAGS_ACK:
            if n_ack > self.last_ack_received:
                self.cwnd += MSS / self.cwnd
            self.last_ack_received = n_ack
            if self.pck_sem_ack:
                self._timeout_interval()
                self.timer.cancel()
                self.pck_sem_ack.pop(0)
                if self.pck_sem_ack:
                    self.timer = asyncio.get_event_loop().call_later(self.timeout_int, self._exemplo_timer)

        # Se pedir para encerrar a conexao
        if (flags & FLAGS_FIN) == FLAGS_FIN:
            payload = b''
            self.n_ack += 1
            
            # Informar a camada de aplicação sobre o fechamento da conexão
            self.callback(self, payload)
            
            # Construindo e enviando pacote ACK
            dst_addr, dst_port, src_addr, src_port = self.id_conexao
            seg = make_header(src_port, dst_port, self.n_seq_base, self.n_ack, FLAGS_ACK)
            seg_fixed_checksum = fix_checksum(seg, src_addr, dst_addr)
            self.servidor.rede.enviar(seg_fixed_checksum, dst_addr)
        elif len(payload) <= 0:
            return

	# Se pacote nao é duplicado
        if n_seq != self.n_ack:
            return

        self.callback(self, payload)
        self.n_ack += len(payload)

        # Construindo e enviando pacote ACK
        dst_addr, dst_port, src_addr, src_port = self.id_conexao
        seg = make_header(src_port, dst_port, self.n_seq_base, self.n_ack, FLAGS_ACK)
        seg_fixed_checksum = fix_checksum(seg, src_addr, dst_addr)

        self.servidor.rede.enviar(seg_fixed_checksum, dst_addr)

    # Os métodos abaixo fazem parte da API

    #Passo 5
    def _exemplo_timer(self):
        if self.pck_sem_ack:
            seg, _, dst_addr, _ = self.pck_sem_ack[0]
            # Reenviando pacote
            self.servidor.rede.enviar(seg, dst_addr)
            self.pck_sem_ack[0][3] = None
        self.cwnd //= 2
        if self.cwnd < MSS:
            self.cwnd = MSS

    #Passo 6

    def _timeout_interval(self):
        _, _, _, sRTT = self.pck_sem_ack[0]
        if sRTT is None:
            return

        sRTT = round(time(), 5) - sRTT
        if self.estRTT is None:
            self.estRTT = sRTT
            self.devRTT = sRTT/2
        else:
            self.estRTT = 0.875*self.estRTT + 0.125*sRTT
            self.devRTT = 0.75*self.devRTT + 0.25 * abs(sRTT-self.estRTT)

        self.timeout_int = self.estRTT + 4*self.devRTT
    
    def registrar_recebedor(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que dados forem corretamente recebidos
        """
        self.callback = callback

    def enviar(self, dados):
        """
        Usado pela camada de aplicação para enviar dados
        """
        # TODO: implemente aqui o envio de dados.
        # Chame self.servidor.rede.enviar(seg, dest_addr) para enviar o seg
        # que você construir para a camada de rede.

        # Passo 2 e 3

        # Construindo e enviando os pacotes

        dst_addr, dst_port, src_addr, src_port = self.id_conexao

        flags = 0 | FLAGS_ACK

        bytes_sent = 0
        while bytes_sent < len(dados):
            segment_size = min(self.cwnd, len(dados) - bytes_sent, MSS)
            payload = dados[bytes_sent: bytes_sent + segment_size]

            seg = make_header(src_port, dst_port, self.n_seq, self.n_ack, flags)
            seg_fixed_checksum = fix_checksum(seg+payload, src_addr, dst_addr)
            self.servidor.rede.enviar(seg_fixed_checksum, dst_addr)

            self.timer = asyncio.get_event_loop().call_later(self.timeout_int, self._exemplo_timer)
            self.pck_sem_ack.append([seg_fixed_checksum, len(payload), dst_addr, round(time(), 5)])

            # Atualizando n_seq com os dados recém enviados
            self.n_seq += len(payload)
            bytes_sent += segment_size
        pass

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """
        # TODO: implemente aqui o fechamento de conexão
        dst_addr, dst_port, src_addr, src_port = self.id_conexao
        seg = make_header(src_port, dst_port, self.n_seq_base, self.n_ack, FLAGS_FIN)
        seg_fixed_checksum = fix_checksum(seg, src_addr, dst_addr)
        self.servidor.rede.enviar(seg_fixed_checksum, dst_addr)
        self.fechada = True
        pass
