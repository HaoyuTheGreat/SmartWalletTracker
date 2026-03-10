import json
import os

#这个函数我们是在删除那些没有swap交易记录（返回错误）的json文件
def remove_error_file():
  #第一步，我们需要拿到文件夹里所有的文件名，os.listdir返回一个列表，比如["2kCm1RHG.json", "Dd1k91cW.json", "5QNhwHKr.json"]。就是文件夹里所有文件的名字。
  swap_tx_folder = "data/wallets_swap_data"
  wallets_file_names = os.listdir(swap_tx_folder)
  #第二步，我们需要遍历每个文件，读取内容。
  #filename是文件名，比如："2kCm1RHG.json", os.path.join()把文件夹和文件名拼凑成完整路径（"data/wallets_swap_data/2kCm1RHG.json"）
  #然后打开文件，读每个JSON
  for filename in wallets_file_names:
    filepath = os.path.join(swap_tx_folder, filename)
    with open(filepath, "r")as f:
      files_data = json.load(f)
    #第三步，判断和删除：在我的这些文件数据，正常的swap记录的文件是一个list[]，但是如果是返回的错误文件，就是个dict{}
    #在这一步，我们先检查是不是dict，然后再检查是不是包含"error"
    if isinstance(files_data, dict) and files_data.get("error"):
      os.remove(filepath)
      print(f"已删除无效文件: {filename}")


if __name__ == "__main__":
    remove_error_file()
