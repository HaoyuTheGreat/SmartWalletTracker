#Blockchains and Blocks:
#Block: it is a container that holds a batch of transactions, each block contains:
# 1. A list of transactions(transfer, swaps, contract calls, etc)
# 2. A block hash - a unique fingerprint of the block's content
# 3. A parent has - reference to the previous block
# 4. A timestamp
# 5. A slot/block number - its position in the chain.
#Blockchain: it is just blocks linked together in sequence. Each block points back to the previous one via its parent hash.
#Transaction: A single action on the blockchain- for example, "Alice sends 500 USDC to Bob". Each transaction has a unique signature(like a receipt ID).
#Validator: The node that processes transactions and produces blocks. For example, On Solana, validators take turns being the "leader" who builds the next block. 
#they verify that transactions are valid before including them.

#----------------------------------------------WebSocket---------------------------------------
#What is WebSocket: 
#--It is a communication protocol that provides a persistent, two-way connection between a client and a server.
#The problem is solves:
#--With regular HTTP, communication works by polling, meaning that the client will as the server over and over again, and server answers.
#Since WebSocket is persistent and two way connection, its connection stays open unlike HTTP opens and closes each time.

#----------------------------------------------Chain-Hopping / Mixer Pattern---------------------------------------
# What we found:
#   While analyzing BigQuery data, we discovered ~150+ transactions all moving exactly $436,050.633872 USDC,
#   each with DIFFERENT sender-receiver pairs. At first it looked like a bot repeating the same transfer.
#
# What it actually is:
#   The wallet pairs form a CHAIN:
#     Wallet_A -> Wallet_B ($436k)
#     Wallet_B -> Wallet_C ($436k)
#     Wallet_C -> Wallet_D ($436k)
#     ... repeated ~150 times
#   The same money is being "hopped" through a long chain of intermediary wallets.
#
# Why someone does this:
#   - Mixer/Tumbler: obfuscates the money trail so it's hard to trace from origin to final destination
#   - Payment routing: some protocols split large transfers across many hops for privacy or compliance
#   - Legitimate transfers don't hop through 150 wallets - this is suspicious activity
#
# How to detect this in BigQuery (future feature):
#   JOIN transactions ON (receiver = next sender) AND (same amount) AND (within 5 min window)
#   This links individual hops into a full chain, revealing the true origin and final destination.
#   Wallets that only exist as pass-throughs (receive and immediately forward the same amount) are likely bot-controlled.

<<<<<<< HEAD
#Analyzing and learning the python code:
=======


#------------------------------------------collect_traders.py边学边做-----------------------------------------------------
sys.stdout.reconfigure(encoding='utf-8')
#读取.env文件里的Helius API KEY。现在的这个HELIUS_API_KEY就被我env文件里的那个值覆盖了。
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
OUTPUT_DIR = "data"
# DexScreener API 基础地址
DEXSCREENER_BASE_URL = "https://api.dexscreener.com"


def test_connection_DexScreener():
    #把这个网址当为字符串赋值给url，f"..."为f-string, 意思是字符串里面可以嵌入变量，用{}包起来。 这样的话，如果我们要改地址，就只需要改“"https://api.dexscreener.com"“。
    # 用一个简单的请求测试：查询 SOL/USDC 交易对
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q=SOL/USDC"
    #这个网址的服务器返回的所有东西，包括状态码、header、body 等等，都塞进了 response 这个变量里。 timeout = 10 意思是如果服务器在十秒内无反应，就终止并返回错误。
    response = requests.get(url, timeout=10)

    # 检查状态码，200 表示请求成功
    if response.status_code == 200:
        #data 为 dictionary, 这一行是把response里的body部分解析成python dictionary 然后存进data。
        data = response.json()
        #data.get("pairs", []), 意思为从这个大字典里的东西中，只把pairs那部分取出来（pairs为key，把他的值全部取出来），如果找不到就返回[]。
        pair_count = len(data.get("pairs", []))
        print(f"连接成功！返回了 {pair_count} 个交易对")
        return True
    else:
        print(f"连接失败，状态码: {response.status_code}")
        return False
#这个function是用来测试是否能连接到Helius API。
def connect_Helius():
    """
    现在是在检查我们这个文件里的HELIUS_API_KEY是否取到了env文件里的HELIUS_API_KEY的值，如果取到了，现在的HELIUS_API_KEY就被赋值了。
    然后如果没有取到（ex：变量名写错了，或者env文件里没有这个变量），就会返回None，或者空字符串。
    现在的这个 if not HELIUS_API_KEY,就是在检查是不是取出来的值是空值。
    not在python 的规则：
    not None -> True; not "" -> True; not "same thing" -> False;
    所以 if not HELIUS_API_KEY: 的意思就是：


    如果 HELIUS_API_KEY 是 None 或者空字符串：
       → 进入 if，报错退出
    否则（有正常的值）：
       → 跳过 if，继续往下走
    """
#在Python中，打开文件用open(). 
#file = open("filepath", "r", encoding = "utf-8"); "r" = read only, encoding = "utf-8"为指定编码，防止中文乱码
#但是如果只按照以下这么写会有个问题，如果中间出错了，文件不会自动关闭。所以Python有个更好的写法叫with:
#with open("filepath", "r", encoding = "utf-8") as file:  ; with的意思是：打开文件，做完事以后自动关闭，不用手动close(), as file 是给打开的文件取个名字叫file。
#在文件打开后，我们需要读取文件（JSON）， 我们要把JSON 文本变成python可以用的数据：
tokens = json.load(file)
#json.load(file) 会读取文件file的内容，把JSON解析成Python对象。因为我的tokens.json 文件的最外层是 [], 所以tokens会变成一个list，里面的每一个element是一个dict：
[
  {"symbol": "SOL",  "mint": "So11111111111111111111111111111111111111112"},
  {"symbol": "JUP",  "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"},
  {"symbol": "PYTH", "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"},
  {"symbol": "PUMP", "mint": "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"}
]
#{"symbol": "SOL",  "mint": "So11111111111111111111111111111111111111112"}， 这一整个是个dict; "symbol", "mint"是keys，它们对应的是value。所以说每个dict有两个key-value pairs
#所以说如果我们要取JUP的mint地址就是：
tokens[1]["mint"] #第一个[]是在指第几个dict，["mint"]为：从这个dict里取key 为“mint”的value
#所以这整个读取json文件的代码就是：
with open("tokens.json", "r", encoding = "utf-8"):
    tokens = json.load(file)
return tokens





#--------------------------------------------------The code from analyze_wallets.py & swap_data.json:

#在这里面，nativeInput&nativeOutput指的是链上的原生币，所以叫“native”，这个例子是SOL。然后其他所有代币都用tokenInputs&tokenOutputs代表。
#input: I paid; output: I received.
#在这里面的amount的单位是lamports。 SOL amount = amount / 1,000,000,000
"events": {
      "swap": {
        "nativeInput": {
          "account": "AUFHo8kwiLArai4NyLEgLnWxFdz7LiVa6rpfsJtzGTTR",
          "amount": "31449352"
        },
        "nativeOutput": {
          "account": "AUFHo8kwiLArai4NyLEgLnWxFdz7LiVa6rpfsJtzGTTR",
          "amount": "18509054356"
        },
        "tokenInputs": [
          {
            "userAccount": "AUFHo8kwiLArai4NyLEgLnWxFdz7LiVa6rpfsJtzGTTR",
            "tokenAccount": "C3ooDqQoftrXw7v8BfzbmjKSA7UB8JowYRgrLJYs55kb",
            "rawTokenAmount": {
              "tokenAmount": "865200000000000",
              "decimals": 9
            },
            "mint": "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC"
          },
          {
            "userAccount": "AUFHo8kwiLArai4NyLEgLnWxFdz7LiVa6rpfsJtzGTTR",
            "tokenAccount": "C3ooDqQoftrXw7v8BfzbmjKSA7UB8JowYRgrLJYs55kb",
            "rawTokenAmount": {
              "tokenAmount": "1234800000000000",
              "decimals": 9
            },
            "mint": "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC"
          }
        ],
        "tokenOutputs": [],
        "nativeFees": [],
        "tokenFees": [],
        "innerSwaps": []
      }
    }

>>>>>>> 79399fcc9ffe9383dfec7db4ec040bfcecb7df24
