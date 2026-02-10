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