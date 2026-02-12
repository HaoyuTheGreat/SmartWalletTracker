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