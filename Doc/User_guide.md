# User guide
## Pixel analysis from videos
To start using your videos, simply drag and drop your files :

![freezingPointv1_drag_videos_edit](https://github.com/user-attachments/assets/84ef5256-5f62-41b7-9e24-1b27727489ee)

You may need to exclude part of the scene (timestamp, area outside the experimental setup) : adjust the green rectangle to the region of interest :

![freezingPointv1_video_crop](https://github.com/user-attachments/assets/d1e013a9-834d-45c7-8c5e-ddf9451d0aab)

Know you can launch the analysis with the **PROCESS** button

## Track analysis from poses track files
If you have used tracking software [SLEAP](https://github.com/talmolab/sleap) or [DEEPLABCUT](https://github.com/DeepLabCut/DeepLabCut), you can use the results files in CSV format for your freezing analysis.

To start drag and drop your files :

![freezingPointv1_drag_tracks](https://github.com/user-attachments/assets/f7a6ab58-ad74-45d9-bedd-48ab49a7b486)

Now define the names you have used for your node labels.

![freezingPointv1_matchLabels](https://github.com/user-attachments/assets/77cbb741-1526-418e-862e-8b6074e08c69)

Now you can launch the analysis by pressing **PROCESS**

When all files are processed a folder named "pixFreezResults" is created in the folder containing source files (video, tracks)
In this folder we found one csv file per source file with specific analysis, one csv file summarizing the analysis and a json file containing all the values processed for all files.

<img width="400" height="177" alt="results_files_small" src="https://github.com/user-attachments/assets/bc84f76d-bb10-4aea-b9a6-ca2d8d3ce2e5" />

Click on **Check** on the processing interface to open the visualisation part of the software

![freezingPointv1_check_results](https://github.com/user-attachments/assets/80c4bef8-fb8d-4522-bae6-036504bae3aa)

<div align="justify">



