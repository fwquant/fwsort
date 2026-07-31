from tailscale import Tailscale


async def simple_tailscale():
    ts = Tailscale(api_key="hskey -auth-oAypjq9_9WtV-XSB1tavhkxmmYgtFPddHAJ8WXOhNBMTCp1T-AEPXAgUKUqu7zPYpqScNBh-PW7jt")

    print(f"已连接: ts:{ts}")

# tailscale.exe up --reset --login-server=https://yaluo.com --auth-key="粘贴一次性密钥" --hostname="windows-client" --accept-dns=true
if __name__ == '__main__':
    import asyncio

    asyncio.run(simple_tailscale())
