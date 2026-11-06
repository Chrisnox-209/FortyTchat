#!/usr/bin/env python3
"""Local-only relay load test; no UDP, no school network traffic, no files."""
import argparse
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.networking.client import ChatClient
from src.networking.relay import RelayServer


async def run(count):
    server = RelayServer()
    port = await server.start(port=0)
    deliveries = [0] * count
    def receiver(index):
        def callback(kind, data):
            if kind == 'event' and data['frame']['type'] == 'message':
                deliveries[index] += 1
        return callback
    clients = [ChatClient(f'Test{i:03}', receiver(i), discovery=False, tcp_port=0,
                          relay_host='127.0.0.1', relay_port=port, tls=False) for i in range(count)]
    started = time.monotonic()
    try:
        await asyncio.gather(*(client.start() for client in clients))
        deadline = time.monotonic() + 30
        while not all(len(client.peers) == count - 1 for client in clients):
            if time.monotonic() > deadline:
                raise RuntimeError('Toutes les présences ne sont pas arrivées en 30 s')
            await asyncio.sleep(0.05)
        await clients[0].send('message', 'general', text='Test local de diffusion')
        deadline = time.monotonic() + 10
        while not all(n == 1 for n in deliveries[1:]):
            if time.monotonic() > deadline:
                raise RuntimeError('Diffusion incomplète ou dupliquée')
            await asyncio.sleep(0.02)
        assert deliveries[0] == 0
        print(f'{count} clients, {count-1} réceptions uniques, {time.monotonic()-started:.2f} s. Test local uniquement.')
    finally:
        await asyncio.gather(*(client.stop() for client in clients))
        await server.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients', type=int, default=40)
    args = parser.parse_args()
    if not 2 <= args.clients <= 300:
        parser.error('Choisir entre 2 et 300 clients')
    asyncio.run(run(args.clients))
